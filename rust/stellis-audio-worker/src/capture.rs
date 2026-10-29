//! Device-local microphone and system-audio capture; frames stay on separate rails.

use base64::{engine::general_purpose::STANDARD, Engine as _};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Device, FromSample, Host, Sample, SampleFormat, SizedSample, Stream, StreamConfig};
use serde::Serialize;
use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

#[derive(Serialize)]
struct Frame {
    stream: &'static str,
    offset_ms: u64,
    sample_rate: u32,
    channels: u16,
    pcm: String,
}

enum Event {
    Frame(Frame),
    Error(String),
}

#[cfg(target_os = "linux")]
fn host() -> Result<Host, String> {
    for id in [cpal::HostId::PulseAudio, cpal::HostId::PipeWire] {
        if cpal::available_hosts().contains(&id) {
            if let Ok(host) = cpal::host_from_id(id) {
                return Ok(host);
            }
        }
    }
    Err("PipeWire or PulseAudio is required for system-audio capture".into())
}

#[cfg(not(target_os = "linux"))]
fn host() -> Result<Host, String> {
    Ok(cpal::default_host())
}

#[cfg(target_os = "linux")]
fn device_name(device: &Device) -> String {
    device.description().map(|description| description.name().to_ascii_lowercase())
        .unwrap_or_default()
}

fn devices(host: &Host) -> Result<(Device, Device), String> {
    #[cfg(target_os = "linux")]
    let inputs: Vec<Device> = host.input_devices().map_err(|e| e.to_string())?.collect();
    #[cfg(target_os = "linux")]
    let microphone = host.default_input_device()
        .filter(|device| !device_name(device).contains(".monitor"))
        .or_else(|| inputs.iter().find(|device| !device_name(device).contains(".monitor")).cloned())
        .ok_or("No microphone input device is available")?;
    #[cfg(not(target_os = "linux"))]
    let microphone = host.default_input_device()
        .ok_or("No microphone input device is available")?;
    #[cfg(target_os = "linux")]
    let system = inputs.into_iter()
        .find(|device| device_name(device).contains(".monitor"))
        .or_else(|| host.default_output_device().filter(|device| device.supports_input()))
        .ok_or("No PipeWire/PulseAudio monitor source is available")?;
    #[cfg(not(target_os = "linux"))]
    let system = host.default_output_device()
        .ok_or("No system output device is available for loopback")?;
    Ok((microphone, system))
}

fn input_stream<T>(
    device: &Device,
    config: StreamConfig,
    stream: &'static str,
    sender: mpsc::SyncSender<Event>,
    overflow: Arc<AtomicBool>,
    began: Instant,
) -> Result<Stream, String>
where
    T: SizedSample + Sample,
    f32: FromSample<T>,
{
    let rate = config.sample_rate;
    let channels = config.channels;
    let error_sender = sender.clone();
    device.build_input_stream(
        config,
        move |data: &[T], _| {
            let mut bytes = Vec::with_capacity(data.len() * 4);
            for sample in data {
                bytes.extend_from_slice(&f32::from_sample(*sample).to_le_bytes());
            }
            let frame = Frame {
                stream,
                offset_ms: began.elapsed().as_millis() as u64,
                sample_rate: rate,
                channels,
                pcm: STANDARD.encode(bytes),
            };
            if sender.try_send(Event::Frame(frame)).is_err() {
                overflow.store(true, Ordering::Release);
            }
        },
        move |error| {
            let _ = error_sender.try_send(Event::Error(error.to_string()));
        },
        None,
    ).map_err(|e| e.to_string())
}

fn open_stream(
    device: &Device,
    stream: &'static str,
    sender: mpsc::SyncSender<Event>,
    overflow: Arc<AtomicBool>,
    began: Instant,
) -> Result<Stream, String> {
    let supported = device.default_input_config().map_err(|e| e.to_string())?;
    let format = supported.sample_format();
    let config: StreamConfig = supported.into();
    macro_rules! build {
        ($type:ty) => { input_stream::<$type>(device, config, stream, sender, overflow, began) };
    }
    match format {
        SampleFormat::F32 => build!(f32),
        SampleFormat::F64 => build!(f64),
        SampleFormat::I8 => build!(i8),
        SampleFormat::I16 => build!(i16),
        SampleFormat::I24 => build!(cpal::I24),
        SampleFormat::I32 => build!(i32),
        SampleFormat::I64 => build!(i64),
        SampleFormat::U8 => build!(u8),
        SampleFormat::U16 => build!(u16),
        SampleFormat::U24 => build!(cpal::U24),
        SampleFormat::U32 => build!(u32),
        SampleFormat::U64 => build!(u64),
        _ => Err(format!("Unsupported capture sample format: {format}")),
    }
}

pub fn run() -> Result<(), String> {
    let host = host()?;
    let (microphone, system) = devices(&host)?;
    let began = Instant::now();
    let (sender, receiver) = mpsc::sync_channel::<Event>(64);
    let overflow = Arc::new(AtomicBool::new(false));
    let mic = open_stream(&microphone, "microphone", sender.clone(), overflow.clone(), began)?;
    let output = open_stream(&system, "system", sender.clone(), overflow.clone(), began)?;
    let writer = std::thread::spawn(move || -> Result<(), String> {
        let stdout = io::stdout();
        let mut out = stdout.lock();
        for event in receiver {
            match event {
                Event::Frame(frame) => {
                    serde_json::to_writer(&mut out, &frame).map_err(|e| e.to_string())?;
                    out.write_all(b"\n").map_err(|e| e.to_string())?;
                    out.flush().map_err(|e| e.to_string())?;
                }
                Event::Error(error) => return Err(error),
            }
        }
        Ok(())
    });
    let (stop_sender, stop_receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = io::stdin().lock().lines().next();
        let _ = stop_sender.send(());
    });
    mic.play().map_err(|e| e.to_string())?;
    output.play().map_err(|e| e.to_string())?;
    while stop_receiver.try_recv().is_err() {
        if overflow.load(Ordering::Acquire) {
            break;
        }
        if writer.is_finished() {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    drop(mic);
    drop(output);
    drop(sender);
    writer.join().map_err(|_| "Capture writer panicked".to_string())??;
    if overflow.load(Ordering::Acquire) {
        return Err("Capture could not keep up; stop and recover saved frames".into());
    }
    Ok(())
}
