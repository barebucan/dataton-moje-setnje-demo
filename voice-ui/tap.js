class VoiceOutputTap extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Float32Array(2048);this.used=0;this.start=0;}
  process(inputs,outputs){
    const output=outputs[0]?.[0];if(output)output.fill(0);
    const input=inputs[0]?.[0]||output||new Float32Array(128);
    for(let i=0;i<input.length;i++){
      if(this.used===0)this.start=currentFrame+i;
      this.samples[this.used++]=input[i];
      if(this.used===this.samples.length){this.port.postMessage({start_frame:this.start,samples:this.samples},[this.samples.buffer]);this.samples=new Float32Array(2048);this.used=0;}
    }return true;
  }
}
registerProcessor('voice-output-tap',VoiceOutputTap);
