// A voice sample as the bot takes one: mono 16-bit PCM in a WAV file, the one kind Python's wave
// module reads, at 16 kHz, the rate at which VoxCPM2 reads a reference clip.
const RATE = 16000;

/** Decodes a recording or an audio file, and encodes it again as a sample to clone from. */
export async function toSample(audio: Blob): Promise<{ wav: Blob; seconds: number }> {
  // An offline context decodes without playing anything, and resamples what it decodes to its own
  // rate. It takes any format the browser can play.
  const context = new OfflineAudioContext(1, 1, RATE);
  // ASSUMPTION (unverified): each browser's decodeAudioData decodes its own MediaRecorder output.
  // Chrome's WebM/Opus and Firefox's Ogg/Opus are backed by their engines' own decode tests;
  // Safari's MP4 is not, and when the decode fails the page asks for a file upload instead.
  const decoded = await context.decodeAudioData(await audio.arrayBuffer());
  return { wav: encode(mono(decoded)), seconds: decoded.duration };
}

/** The channels averaged into one: decoding resamples, but keeps every channel. */
function mono(audio: AudioBuffer): Float32Array {
  const samples = new Float32Array(audio.length);
  for (let channel = 0; channel < audio.numberOfChannels; channel++) {
    audio.getChannelData(channel).forEach((value, i) => (samples[i] += value / audio.numberOfChannels));
  }
  return samples;
}

/** A WAV file: a 44-byte header, then each sample as a 16-bit integer, little-endian. */
function encode(samples: Float32Array): Blob {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset: number, value: string) =>
    [...value].forEach((letter, i) => view.setUint8(offset + i, letter.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true); // the size of the rest of the file
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true); // the size of the format chunk
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // one channel
  view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 2, true); // bytes per second
  view.setUint16(32, 2, true); // bytes per sample
  view.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, value)) * 0x7fff, true));
  return new Blob([view], { type: "audio/wav" });
}
