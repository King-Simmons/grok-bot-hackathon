import sys, numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, scipy.io.wavfile as wf
src, out, title, bpm = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sr, x = wf.read(src); x = x.astype(float); m = (x.mean(1) if x.ndim > 1 else x) / 32767; t = np.arange(len(m)) / sr
fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
ax[0].plot(t, m, lw=.3, color="#c9a"); ax[0].set_ylim(-1, 1); ax[0].set_title(f"{title} — waveform (bar lines @ {bpm:g} BPM)")
bar = 240 / bpm
for b in range(int(t[-1] / bar) + 1): ax[0].axvline(b * bar, color="k", alpha=.15)
ax[1].specgram(m + 1e-9, NFFT=4096, Fs=sr, noverlap=3072, cmap="magma", vmin=-120); ax[1].set_ylim(0, 10000); ax[1].set_ylabel("Hz"); ax[1].set_xlabel("s")
plt.tight_layout(); plt.savefig(out, dpi=90)
