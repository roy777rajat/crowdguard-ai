import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { toWav16k } from '../audio/wav';
import { ApiError, api } from '../api/client';
import Markdown from '../components/Markdown';
import { Empty, ErrorBox, Loading } from '../components/States';
import { useRecorder } from '../hooks/useRecorder';
import type { AssistantCaps, AssistantReply } from '../types';
import '../styles/assistant.css';

interface Msg {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  ts: number;
  voice?: boolean;
  reply?: AssistantReply;
}

type VoiceStage = 'idle' | 'converting' | 'transcribing';

let nextId = 1;
const time = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export default function Assistant() {
  const [caps, setCaps] = useState<AssistantCaps | null>(null);
  const [capsError, setCapsError] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState('');
  const [fromVoice, setFromVoice] = useState(false);
  const [busy, setBusy] = useState(false);
  const [chatError, setChatError] = useState<{ text: string; retry: () => void } | null>(null);
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('idle');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceInfo, setVoiceInfo] = useState<string | null>(null);
  const [language, setLanguage] = useState('auto');
  const [convId, setConvId] = useState<string | null>(null);
  const [actionState, setActionState] = useState<Record<string, string>>({});
  const endRef = useRef<HTMLDivElement | null>(null);

  const loadCaps = useCallback(() => {
    setCapsError(null);
    api.assistantCaps().then(setCaps).catch((e: Error) => setCapsError(e.message));
  }, []);
  useEffect(loadCaps, [loadCaps]);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [msgs, busy]);

  const onClip = useCallback(
    async (clip: Blob) => {
      setVoiceError(null);
      setVoiceInfo(null);
      try {
        setVoiceStage('converting');
        const wav = await toWav16k(clip);
        setVoiceStage('transcribing');
        const r = await api.assistantTranscribe(wav, language);
        setDraft(r.transcript); // shown in the composer so it can be corrected before sending
        setFromVoice(true);
        setVoiceInfo(`Transcribed ${r.audio_seconds.toFixed(1)} s of audio (${r.model}). If words are missing, record again or type them.`);
      } catch (e) {
        setVoiceError(e instanceof ApiError || e instanceof Error ? e.message : 'Transcription failed.');
      } finally {
        setVoiceStage('idle');
      }
    },
    [language],
  );
  const rec = useRecorder((c) => void onClip(c));

  const ask = useCallback(
    async (text: string, voice: boolean, conv: string | null) => {
      setBusy(true);
      setChatError(null);
      try {
        const reply = await api.assistantChat(text, conv, voice ? 'voice' : 'text');
        if (reply.conversation_id) setConvId(reply.conversation_id);
        setMsgs((m) => [
          ...m,
          {
            id: nextId++,
            role: 'assistant',
            ts: Date.now(),
            text: reply.answer ?? reply.message ?? 'No answer was returned.',
            reply,
          },
        ]);
      } catch (e) {
        const message = e instanceof Error ? e.message : 'The question could not be sent.';
        setChatError({ text: message, retry: () => void ask(text, voice, conv) });
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setMsgs((m) => [...m, { id: nextId++, role: 'user', text, ts: Date.now(), voice: fromVoice }]);
    setDraft('');
    const wasVoice = fromVoice;
    setFromVoice(false);
    void ask(text, wasVoice, convId);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) submit(e);
  };

  const decide = async (id: string, yes: boolean) => {
    setActionState((s) => ({ ...s, [id]: 'working' }));
    try {
      const r = yes ? await api.assistantConfirm(id) : await api.assistantCancel(id);
      setActionState((s) => ({ ...s, [id]: yes ? `Done: ${(r as { summary?: string }).summary ?? 'applied'}` : 'Declined, nothing changed' }));
    } catch (e) {
      setActionState((s) => ({ ...s, [id]: `Failed: ${e instanceof Error ? e.message : 'error'}` }));
    }
  };

  const cancelAll = async () => {
    try {
      await api.assistantCancelAll();
      setActionState((s) => {
        const next = { ...s };
        msgs.forEach((m) => m.reply?.pending_actions.forEach((a) => { if (!next[a.action_id]) next[a.action_id] = 'Cancelled'; }));
        return next;
      });
    } catch (e) {
      setChatError({ text: e instanceof Error ? e.message : 'Could not cancel.', retry: () => undefined });
    }
  };

  const newChat = () => {
    setMsgs([]);
    setConvId(null);
    setChatError(null);
  };

  if (capsError) return <ErrorBox message={capsError} onRetry={loadCaps} />;
  if (!caps) return <Loading label="Loading the assistant" />;

  const chatOk = caps.chat.available;
  const recording = rec.state === 'recording';
  const voiceBusy = voiceStage !== 'idle';

  return (
    <div className="assistant">
      <div className="page-head">
        <h1>AI Assistant</h1>
        <div className="sub">
          Ask about pandals, cameras, incidents, forecasts and system status. Answers come from CrowdGuard records
          through read-only tools, explained by {caps.chat.model ?? 'Meta Muse Spark'}. All monitoring data is simulated.
        </div>
      </div>

      {!chatOk && (
        <div className="alert error" role="alert">
          The assistant is not available. {caps.chat.reason} No model is called, so no answers will be shown.
        </div>
      )}

      <div className="chat card">
        <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
          {msgs.length === 0 && (
            <Empty>
              <p>Try one of these{caps.suggestions.length ? '' : ' once the assistant is available'}:</p>
              <div className="suggest">
                {caps.suggestions.map((s) => (
                  <button key={s} className="chip" onClick={() => { setDraft(s); setFromVoice(false); }}>{s}</button>
                ))}
              </div>
            </Empty>
          )}
          {msgs.map((m) => (
            <div key={m.id} className={`bubble ${m.role}${m.reply && m.reply.status !== 'ok' ? ' warn' : ''}`}>
              <div className="meta">
                {m.role === 'user' ? 'You' : 'Assistant'}
                {m.voice && <span className="tag">voice</span>}
                <span className="time">{time(m.ts)}</span>
              </div>
              {m.role === 'assistant' ? <Markdown text={m.text} /> : <p>{m.text}</p>}
              {m.reply && (
                <div className="detail">
                  {m.reply.tools.length > 0 && (
                    <div className="tools" aria-label="Tools used">
                      {m.reply.tools.map((t, i) => (
                        <span key={i} className={`tool ${t.ok ? 'ok' : 'bad'}`} title={t.error_code ?? 'ok'}>
                          {t.ok ? '✓' : '✗'} {t.name}
                        </span>
                      ))}
                    </div>
                  )}
                  {m.reply.sources.length > 0 && (
                    <details>
                      <summary>Sources ({m.reply.sources.length})</summary>
                      <ul>
                        {m.reply.sources.map((s) => (
                          <li key={s.tool}>
                            <code>{s.source}</code>
                            {s.as_of ? ` · read ${new Date(s.as_of * 1000).toLocaleTimeString()}` : ''}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {m.reply.pending_actions.map((a) => (
                    <div key={a.action_id} className="pending" role="group" aria-label="Action awaiting confirmation">
                      <div><strong>{a.destructive ? 'Permanent change, needs your reply:' : 'Needs your reply:'}</strong> {a.summary}</div>
                      {actionState[a.action_id] && actionState[a.action_id] !== 'working' ? (
                        <div className="sub">{actionState[a.action_id]}</div>
                      ) : (
                        <div className="row">
                          <button className={`btn small ${a.destructive ? 'danger' : 'primary'}`} disabled={actionState[a.action_id] === 'working'} onClick={() => void decide(a.action_id, true)}>Yes</button>
                          <button className="btn small" disabled={actionState[a.action_id] === 'working'} onClick={() => void decide(a.action_id, false)}>No</button>
                          <button className="btn small" disabled={actionState[a.action_id] === 'working'} onClick={() => void cancelAll()}>Cancel</button>
                          <span className="sub">You can also type or say Yes, No or Cancel.</span>
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="sub foot">
                    {m.reply.model ?? 'no model'} · {m.reply.turns} step{m.reply.turns === 1 ? '' : 's'}
                    {m.reply.duration_ms !== null && ` · ${(m.reply.duration_ms / 1000).toFixed(1)} s`} ·{' '}
                    {m.reply.usage.input_tokens === null ? 'tokens not reported' : `${m.reply.usage.input_tokens} in / ${m.reply.usage.output_tokens} out tokens`}
                    {' · ref '}<code>{m.reply.correlation_id.slice(0, 8)}</code>
                  </div>
                </div>
              )}
            </div>
          ))}
          {busy && (
            <div className="bubble assistant" role="status">
              <span className="dots" aria-hidden="true" /> Reading CrowdGuard data and asking the model…
            </div>
          )}
          {chatError && (
            <div className="alert error" role="alert">
              {chatError.text}{' '}
              <button className="btn small" onClick={chatError.retry}>Retry</button>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={submit}>
          {voiceStage !== 'idle' && (
            <div className="alert ok" role="status">
              {voiceStage === 'converting' ? 'Preparing your recording…' : 'Transcribing with Muse Voice Transcribe…'}
            </div>
          )}
          {voiceError && <div className="alert error" role="alert">{voiceError}</div>}
          {rec.message && <div className="alert error" role="alert">{rec.message}</div>}
          {fromVoice && !voiceBusy && <div className="sub">Transcribed from voice. Check the text, fix anything wrong, then send. {voiceInfo}</div>}
          <textarea
            aria-label="Your question"
            value={draft}
            rows={2}
            maxLength={1500}
            placeholder={chatOk ? 'Ask a question…' : 'The assistant is not available'}
            disabled={!chatOk}
            onChange={(e) => { setDraft(e.target.value); }}
            onKeyDown={onKey}
          />
          <div className="row">
            {caps.voice.available ? (
              rec.state === 'unsupported' ? (
                <span className="sub" role="note">Voice input is not supported in this browser. Please type your question.</span>
              ) : (
                <>
                  <button
                    type="button"
                    className={`btn mic${recording ? ' live' : ''}`}
                    aria-pressed={recording}
                    disabled={!chatOk || voiceBusy || rec.state === 'requesting'}
                    onClick={() => (recording ? rec.stop() : void rec.start())}
                  >
                    {recording ? `■ Stop (${rec.seconds}s)` : rec.state === 'requesting' ? 'Starting microphone…' : '🎤 Speak'}
                  </button>
                  <label className="sub">
                    Language{' '}
                    <select value={language} onChange={(e) => setLanguage(e.target.value)} disabled={recording || voiceBusy}>
                      {caps.voice.languages.map((l) => <option key={l} value={l}>{l === 'auto' ? 'Auto-detect' : l}</option>)}
                    </select>
                  </label>
                </>
              )
            ) : (
              <span className="sub" role="note">Voice input is off. {caps.voice.reason}</span>
            )}
            <span className="grow" />
            <button type="button" className="btn small" onClick={newChat} disabled={busy || msgs.length === 0}>New chat</button>
            <button type="submit" className="btn primary" disabled={!chatOk || busy || voiceBusy || !draft.trim()}>Send</button>
          </div>
        </form>
      </div>
      <p className="sub fine">
        Decision support for authorized people, not an instruction. Voice audio is sent to Meta Muse Voice Transcribe
        only when you use the microphone; it is not stored here. The transcript is shown above so you can correct it.
      </p>
    </div>
  );
}
