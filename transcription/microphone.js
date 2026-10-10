class MicrophoneProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new ArrayBuffer(Math.round(sampleRate * 0.08) * 2);
    this.view = new DataView(this.buffer);
    this.offset = 0;

    this.port.onmessage = ({ data }) => {
      if (data === "flush") {
        if (this.offset > 0) {
          this.port.postMessage(this.buffer.slice(0, this.offset));
          this.offset = 0;
        }
        this.port.postMessage("flushed");
      }
    };
  }

  process(inputs) {
    const samples = inputs[0]?.[0];
    if (!samples) return true;

    for (const sample of samples) {
      const value = Math.max(-1, Math.min(1, sample));
      this.view.setInt16(
        this.offset,
        Math.round(value < 0 ? value * 32768 : value * 32767),
        true
      );
      this.offset += 2;

      if (this.offset === this.buffer.byteLength) {
        this.port.postMessage(this.buffer.slice(0));
        this.offset = 0;
      }
    }

    return true;
  }
}

registerProcessor("microphone-processor", MicrophoneProcessor);
