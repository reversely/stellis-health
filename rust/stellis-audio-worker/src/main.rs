//! Incremental local ASR for verified recording audio supplied by Stellis.

#[cfg(feature = "native-capture")]
mod capture;

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde::{Deserialize, Serialize};
use sherpa_onnx::{OfflineRecognizer, OfflineRecognizerConfig, OnlineRecognizer, OnlineRecognizerConfig};
use std::io::{self, BufRead, Write};
use std::path::Path;

const RATE: i32 = 16_000;

#[derive(Deserialize)]
struct Request {
    pcm: String,
    #[serde(default)]
    finish: bool,
}

#[derive(Clone, Serialize)]
struct Segment {
    start_ms: u64,
    end_ms: u64,
    text: String,
}

#[derive(Serialize)]
struct Reply {
    segments: Vec<Segment>,
    partial: String,
    partial_start_ms: u64,
    duration_ms: u64,
}

fn online_model(dir: &Path) -> Result<OnlineRecognizer, String> {
    let mut config = OnlineRecognizerConfig::default();
    let model = &mut config.model_config;
    model.transducer.encoder = Some(model_file(dir, &["encoder.int8.onnx", "encoder.onnx", "encoder-epoch-99-avg-1.int8.onnx"])?);
    model.transducer.decoder = Some(model_file(dir, &["decoder.int8.onnx", "decoder.onnx", "decoder-epoch-99-avg-1.onnx"])?);
    model.transducer.joiner = Some(model_file(dir, &["joiner.int8.onnx", "joiner.onnx", "joiner-epoch-99-avg-1.int8.onnx"])?);
    model.tokens = Some(dir.join("tokens.txt").to_string_lossy().into_owned());
    model.num_threads = 2;
    config.enable_endpoint = true;
    config.decoding_method = Some("greedy_search".into());
    OnlineRecognizer::create(&config).ok_or_else(|| "Could not load online ASR model".into())
}

fn parakeet_model(dir: &Path) -> Result<OfflineRecognizer, String> {
    let mut config = OfflineRecognizerConfig::default();
    let model = &mut config.model_config;
    model.transducer.encoder = Some(model_file(dir, &["encoder.onnx", "encoder.int8.onnx"])?);
    model.transducer.decoder = Some(model_file(dir, &["decoder.onnx", "decoder.int8.onnx"])?);
    model.transducer.joiner = Some(model_file(dir, &["joiner.onnx", "joiner.int8.onnx"])?);
    model.tokens = Some(dir.join("tokens.txt").to_string_lossy().into_owned());
    model.model_type = Some("nemo_transducer".into());
    model.num_threads = 2;
    OfflineRecognizer::create(&config).ok_or_else(|| "Could not load Parakeet model".into())
}

fn model_file(dir: &Path, names: &[&str]) -> Result<String, String> {
    names.iter().map(|name| dir.join(name)).find(|path| path.is_file())
        .map(|path| path.to_string_lossy().into_owned())
        .ok_or_else(|| format!("Missing model file in {}", dir.display()))
}

fn samples(encoded: &str) -> Result<Vec<f32>, String> {
    let bytes = STANDARD.decode(encoded).map_err(|_| "Invalid PCM encoding")?;
    if bytes.len() % 4 != 0 || bytes.len() > 16_000_000 {
        return Err("Invalid PCM length".into());
    }
    Ok(bytes.chunks_exact(4).map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap())).collect())
}

fn run(online_dir: &Path, parakeet_dir: &Path) -> Result<(), String> {
    let online = online_model(online_dir)?;
    let parakeet = parakeet_model(parakeet_dir)?;
    let stream = online.create_stream();
    let mut utterance = Vec::<f32>::new();
    let mut segment_start: u64 = 0;
    let mut sample_count: u64 = 0;
    let mut partial = String::new();
    let mut stdout = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let line = line.map_err(|error| error.to_string())?;
        let request: Request = serde_json::from_str(&line).map_err(|error| error.to_string())?;
        let incoming = samples(&request.pcm)?;
        sample_count += incoming.len() as u64;
        utterance.extend_from_slice(&incoming);
        // Bound offline input when the recorder stays open through long silence.
        if partial.is_empty() && utterance.len() > 120 * RATE as usize {
            let excess = utterance.len() - 120 * RATE as usize;
            utterance.drain(..excess);
            segment_start += excess as u64;
        }
        stream.accept_waveform(RATE, &incoming);
        if request.finish {
            stream.accept_waveform(RATE, &vec![0.0; (RATE / 2) as usize]);
            stream.input_finished();
        }
        let mut segments = Vec::<Segment>::new();
        while online.is_ready(&stream) {
            online.decode(&stream);
            if let Some(result) = online.get_result(&stream) {
                partial = result.text.trim().to_owned();
            }
            if online.is_endpoint(&stream) {
                let utterance_stream = parakeet.create_stream();
                utterance_stream.accept_waveform(RATE, &utterance);
                parakeet.decode(&utterance_stream);
                if let Some(result) = utterance_stream.get_result() {
                    let text = result.text.trim();
                    if !text.is_empty() {
                        segments.push(Segment {
                            start_ms: segment_start * 1000 / RATE as u64,
                            end_ms: sample_count * 1000 / RATE as u64,
                            text: text.into(),
                        });
                    }
                }
                utterance.clear();
                segment_start = sample_count;
                online.reset(&stream);
                partial.clear();
            }
        }
        if request.finish && !utterance.is_empty() {
            let utterance_stream = parakeet.create_stream();
            utterance_stream.accept_waveform(RATE, &utterance);
            parakeet.decode(&utterance_stream);
            if let Some(result) = utterance_stream.get_result() {
                let text = result.text.trim();
                if !text.is_empty() {
                    segments.push(Segment {
                        start_ms: segment_start * 1000 / RATE as u64,
                        end_ms: sample_count * 1000 / RATE as u64,
                        text: text.into(),
                    });
                }
            }
        }
        let reply = Reply {
            segments,
            partial: partial.clone(),
            partial_start_ms: segment_start * 1000 / RATE as u64,
            duration_ms: sample_count * 1000 / RATE as u64,
        };
        serde_json::to_writer(&mut stdout, &reply).map_err(|error| error.to_string())?;
        stdout.write_all(b"\n").map_err(|error| error.to_string())?;
        stdout.flush().map_err(|error| error.to_string())?;
        if request.finish { break; }
    }
    Ok(())
}

fn main() {
    #[cfg(feature = "native-capture")]
    let mut args = std::env::args_os().skip(1);
    #[cfg(feature = "native-capture")]
    if args.next().as_deref() == Some(std::ffi::OsStr::new("capture")) {
        if let Err(error) = capture::run() {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    let mut args = std::env::args_os().skip(1);
    let online = args.next();
    let parakeet = args.next();
    if online.is_none() || parakeet.is_none() || args.next().is_some() {
        eprintln!("Usage: stellis-audio-worker ONLINE_MODEL_DIR PARAKEET_MODEL_DIR");
        std::process::exit(2);
    }
    if let Err(error) = run(Path::new(online.as_ref().unwrap()), Path::new(parakeet.as_ref().unwrap())) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
