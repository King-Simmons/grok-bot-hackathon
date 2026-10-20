# synthetic 'vocal': formant-filtered pulse train, syllables on the beat at 90 BPM, 20 s
import os
import numpy as np, scipy.signal as ss, scipy.io.wavfile as wf
SR=44100; bpm=90; n=int(20*SR); t=np.arange(n)/SR; rng=np.random.default_rng(0)
f0=110*(1+0.05*np.sin(2*np.pi*0.3*t)); ph=np.cumsum(f0)/SR; src=(ph%1<0.08).astype(float)-0.08
y=sum(g*ss.lfilter(*ss.iirpeak(f,8,fs=SR),src) for f,g in [(700,1),(1200,.6),(2600,.3)])
gate=np.zeros(n); beat=60/bpm
for k in range(int(20/beat*2)):
    s=int(k*beat/2*SR); L=int(0.18*SR); gate[s:s+L]+=np.hanning(L)
y*=gate; y/=np.abs(y).max(); wf.write(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_vocal_90bpm.wav"),SR,(y*0.8*32767).astype(np.int16))
