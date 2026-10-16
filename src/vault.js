const encoder = new TextEncoder();
const decoder = new TextDecoder();

function toBase64(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value) {
  return Uint8Array.from(atob(value), character => character.charCodeAt(0));
}

export function newSalt() {
  return toBase64(crypto.getRandomValues(new Uint8Array(16)));
}

export async function deriveVaultKey(masterPassword, salt) {
  const material = await crypto.subtle.importKey('raw', encoder.encode(masterPassword), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt: fromBase64(salt), iterations: 600000, hash: 'SHA-256' },
    material, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

export async function encryptValue(key, value) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key,
    encoder.encode(JSON.stringify(value))));
  const packed = new Uint8Array(iv.length + encrypted.length);
  packed.set(iv);
  packed.set(encrypted, iv.length);
  return toBase64(packed);
}

export async function decryptValue(key, ciphertext) {
  const packed = fromBase64(ciphertext);
  const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: packed.slice(0, 12) },
    key, packed.slice(12));
  return JSON.parse(decoder.decode(decrypted));
}

export function generatedPassword(length = 20) {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*';
  const values = crypto.getRandomValues(new Uint32Array(length));
  return Array.from(values, value => alphabet[value % alphabet.length]).join('');
}

export function vaultHealth(items) {
  const counts = new Map();
  for (const item of items) {
    if (item.password) counts.set(item.password, (counts.get(item.password) || 0) + 1);
  }
  return {
    total: items.length,
    reused: items.filter(item => item.password && counts.get(item.password) > 1).length,
    short: items.filter(item => item.password && item.password.length < 12).length
  };
}
