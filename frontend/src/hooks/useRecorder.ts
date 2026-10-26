import { useCallback, useEffect, useRef, useState } from 'react';

export type RecState = 'idle' | 'requesting' | 'recording' | 'unsupported' | 'denied' | 'error';
export const MAX_RECORD_S = 60;

export function micSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof MediaRecorder !== 'undefined'
  );
}

/** Push-to-talk recorder. Calls onClip with the raw recording when the user stops (or the time limit hits). */
export function useRecorder(onClip: (clip: Blob) => void) {
  const [state, setState] = useState<RecState>(() => (micSupported() ? 'idle' : 'unsupported'));
  const [seconds, setSeconds] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const cb = useRef(onClip);
  cb.current = onClip;

  const release = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  useEffect(() => release, [release]);

  const stop = useCallback(() => {
    if (rec.current && rec.current.state !== 'inactive') rec.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (!micSupported()) {
      setState('unsupported');
      return;
    }
    setMessage(null);
    setState('requesting');
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      stream.current = s;
      const r = new MediaRecorder(s);
      chunks.current = [];
      r.ondataavailable = (e: BlobEvent) => {
        if (e.data && e.data.size > 0) chunks.current.push(e.data);
      };
      r.onstop = () => {
        const blob = new Blob(chunks.current, { type: r.mimeType || 'audio/webm' });
        release();
        setState('idle');
        if (blob.size > 0) cb.current(blob);
        else setMessage('Nothing was recorded. Try again.');
      };
      r.onerror = () => {
        release();
        setState('error');
        setMessage('The recording failed. Try again.');
      };
      rec.current = r;
      // Only show "recording" once the recorder has really started, so the user does not talk too early.
      r.onstart = () => {
        setSeconds(0);
        setState('recording');
      };
      r.start(250);
      if (timer.current !== null) window.clearInterval(timer.current);
      timer.current = window.setInterval(() => {
        setSeconds((n) => {
          if (n + 1 >= MAX_RECORD_S) stop();
          return n + 1;
        });
      }, 1000);
    } catch (e) {
      release();
      const name = (e as { name?: string }).name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setState('denied');
        setMessage('Microphone access was blocked. Allow it in the browser address bar, or type your question.');
      } else if (name === 'NotFoundError') {
        setState('error');
        setMessage('No microphone was found. Type your question instead.');
      } else {
        setState('error');
        setMessage('The microphone could not be started. Type your question instead.');
      }
    }
  }, [release, stop]);

  return { state, seconds, message, start, stop };
}
