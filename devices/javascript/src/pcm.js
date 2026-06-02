export function concatBytes(a, b) {
  const left = a instanceof Uint8Array ? a : new Uint8Array(a ?? 0);
  const right = b instanceof Uint8Array ? b : new Uint8Array(b ?? 0);
  const out = new Uint8Array(left.byteLength + right.byteLength);
  out.set(left, 0);
  out.set(right, left.byteLength);
  return out;
}

export function floatToPcm16(samples) {
  const out = new ArrayBuffer(samples.length * 2);
  const view = new DataView(out);
  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.max(-1, Math.min(0.999969, samples[index]));
    view.setInt16(index * 2, Math.round(value * 32767), true);
  }
  return new Uint8Array(out);
}

export function pcm16ToFloat(payload) {
  const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const samples = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 32768;
  }
  return samples;
}

export function resampleLinear(input, sourceRate, targetRate) {
  if (!input.length || sourceRate === targetRate) {
    return input instanceof Float32Array ? input : Float32Array.from(input);
  }
  const ratio = sourceRate / targetRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = index * ratio;
    const left = Math.floor(sourceIndex);
    const right = Math.min(input.length - 1, left + 1);
    const weight = sourceIndex - left;
    output[index] = input[left] * (1 - weight) + input[right] * weight;
  }
  return output;
}
