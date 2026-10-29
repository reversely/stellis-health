/* Same browser crypto contract on both local origins; keys never leave page memory. */
window.StellisCrypto = (() => {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const b64 = value => {
    let text = '';
    for (let offset = 0; offset < value.length; offset += 8192)
      text += String.fromCharCode(...value.subarray(offset, offset + 8192));
    return btoa(text);
  };
  const bytes = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const random = count => crypto.getRandomValues(new Uint8Array(count));
  const aad = (...parts) => encoder.encode(parts.join('|'));
  async function derive(passphrase, salt) {
    const material = await crypto.subtle.importKey('raw', encoder.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:600000,hash:'SHA-256'}, material,
      {name:'AES-GCM',length:256}, false, ['encrypt','decrypt']);
  }
  async function seal(key, value, iv, binding) {
    return b64(new Uint8Array(await crypto.subtle.encrypt(
      {name:'AES-GCM',iv,additionalData:binding,tagLength:128}, key, encoder.encode(value))));
  }
  async function open(key, value, iv, binding) {
    return decoder.decode(await crypto.subtle.decrypt(
      {name:'AES-GCM',iv,additionalData:binding,tagLength:128}, key, bytes(value)));
  }
  async function sealBytes(key, value, iv, binding) {
    return b64(new Uint8Array(await crypto.subtle.encrypt(
      {name:'AES-GCM',iv,additionalData:binding,tagLength:128}, key, value)));
  }
  async function openBytes(key, value, iv, binding) {
    return new Uint8Array(await crypto.subtle.decrypt(
      {name:'AES-GCM',iv,additionalData:binding,tagLength:128}, key, bytes(value)));
  }
  return {b64,bytes,random,aad,derive,seal,open,sealBytes,openBytes};
})();
