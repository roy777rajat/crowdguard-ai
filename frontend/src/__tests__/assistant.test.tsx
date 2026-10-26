import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import Markdown from '../components/Markdown';
import { AuthProvider } from '../hooks/auth';

vi.mock('../audio/wav', () => ({
  toWav16k: vi.fn(async () => new Blob([new Uint8Array(100)], { type: 'audio/wav' })),
  encodeWav16: vi.fn(),
  TARGET_RATE: 16000,
}));

type Handler = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined;

const caps = {
  chat: { available: true, provider: 'meta', model: 'muse-spark-1.3', reason: null },
  voice: { available: true, model: 'muse-voice-transcribe-1.0', languages: ['auto', 'English', 'Hindi'], max_seconds: 60, reason: null },
  tools: [{ name: 'list_incidents', description: 'd', permission: 'read', source: 'GET /api/incidents' }],
  actions: [],
  suggestions: ['Show me unresolved incidents and their pandal names.', 'Which cameras are currently offline?'],
};
const reply = (over: Record<string, unknown> = {}) => ({
  status: 'ok', answer: 'There are **2** unresolved incidents at *Dakshin Sarbojanin Association*.', message: null,
  conversation_id: 'c1', correlation_id: 'abcdef123456', provider: 'meta', model: 'muse-spark-1.3', turns: 2,
  tools: [{ name: 'list_incidents', ok: true, duration_ms: 3, error_code: null }],
  sources: [{ tool: 'list_incidents', source: 'GET /api/incidents', as_of: 1760000000 }],
  pending_actions: [], duration_ms: 1200, usage: { input_tokens: null, output_tokens: null }, disclaimer: 'x', ...over,
});

let calls: Array<{ url: string; init?: RequestInit }> = [];

function stub(extra: Handler = () => undefined, capsOverride: unknown = caps) {
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, init });
    const custom = extra(u, init);
    const path = u.split('?')[0];
    let status = 200;
    let body: unknown;
    if (custom) {
      status = custom.status ?? 200;
      body = custom.body;
    } else if (path === '/api/auth/me') body = { user: { id: 1, username: 'admin', role: 'admin', display_name: 'Admin', pandal_ids: [] } };
    else if (path === '/api/assistant/capabilities') body = capsOverride;
    else if (path === '/api/assistant/chat') body = reply();
    else { status = 404; body = { detail: 'nf' }; }
    return { ok: status < 400, status, statusText: 'x', json: async () => body } as unknown as Response;
  }));
}

function renderAssistant() {
  localStorage.setItem('cg_token', 'abc');
  return render(<MemoryRouter initialEntries={['/assistant']}><AuthProvider><App /></AuthProvider></MemoryRouter>);
}
const box = () => screen.getByLabelText('Your question') as HTMLTextAreaElement;
const chatBodies = () => calls.filter((c) => c.url === '/api/assistant/chat').map((c) => JSON.parse(String(c.init?.body)));

class FakeRecorder {
  static last: FakeRecorder | null = null;
  state = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() { FakeRecorder.last = this; }
  onstart: (() => void) | null = null;
  start() {
    this.state = 'recording';
    this.onstart?.();
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob([new Uint8Array(50)]) });
    this.onstop?.();
  }
}
const stream = { getTracks: () => [{ stop: vi.fn() }] };
function mic(getUserMedia: () => Promise<unknown>) {
  vi.stubGlobal('MediaRecorder', FakeRecorder);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
}

describe('Markdown rendering is safe', () => {
  afterEach(cleanup);
  it('never turns model text into HTML or unsafe links', () => {
    const { container } = render(<Markdown text={'<img src=x onerror=alert(1)> <script>bad()</script>\n\n[ok](https://example.com) [evil](javascript:alert(1))'} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    const links = Array.from(container.querySelectorAll('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.com']);
    expect(links[0].getAttribute('rel')).toContain('noopener');
    expect(container.textContent).toContain('<script>bad()</script>');
  });
  it('renders lists, tables, bold and code', () => {
    const { container } = render(<Markdown text={'**Bold** and `code`\n\n- one\n- two\n\n| Pandal | Count |\n|---|---|\n| A | 3 |'} />);
    expect(container.querySelector('strong')?.textContent).toBe('Bold');
    expect(container.querySelectorAll('li').length).toBe(2);
    expect(container.querySelector('table td')?.textContent).toBe('A');
  });
});

describe('Assistant page', () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, 'mediaDevices');
  });

  it('has a nav entry and shows suggestions built from real capabilities', async () => {
    stub();
    renderAssistant();
    await waitFor(() => expect(screen.getByRole('heading', { name: 'AI Assistant' })).toBeTruthy());
    expect(screen.getByRole('link', { name: 'AI Assistant' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /unresolved incidents/ })).toBeTruthy();
    expect(screen.getByText(/All monitoring data is simulated/)).toBeTruthy();
  });

  it('sends a typed question and shows answer, tools, sources, timestamp and honest usage', async () => {
    stub();
    renderAssistant();
    await waitFor(() => screen.getByLabelText('Your question'));
    fireEvent.change(box(), { target: { value: 'Show me unresolved incidents and their pandal names.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/unresolved incidents at/)).toBeTruthy());
    expect(chatBodies()[0]).toEqual({ message: 'Show me unresolved incidents and their pandal names.', conversation_id: null, input_mode: 'text' });
    expect(screen.getByText('2').tagName).toBe('STRONG');
    expect(screen.getByLabelText('Tools used').textContent).toContain('list_incidents');
    expect(screen.getByText(/Sources \(1\)/)).toBeTruthy();
    expect(screen.getByText(/tokens not reported/)).toBeTruthy();
    expect(screen.getByRole('log').textContent).toMatch(/\d{1,2}:\d{2}/);
    expect(box().value).toBe('');
    // follow-up continues the same conversation
    fireEvent.change(box(), { target: { value: 'and critical ones?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(chatBodies().length).toBe(2));
    expect(chatBodies()[1].conversation_id).toBe('c1');
  });

  it('shows an error with a working Retry', async () => {
    let n = 0;
    stub((u) => (u === '/api/assistant/chat' && n++ === 0 ? { status: 502, body: { detail: 'upstream down' } } : undefined));
    renderAssistant();
    await waitFor(() => screen.getByLabelText('Your question'));
    fireEvent.change(box(), { target: { value: 'hello' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/upstream down/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByText(/unresolved incidents at/)).toBeTruthy());
    expect(chatBodies().length).toBe(2);
  });

  it('is clearly unavailable without a model credential', async () => {
    stub(() => undefined, { ...caps, chat: { available: false, provider: null, model: null, reason: 'No Meta Muse Spark credential is configured on the server.' }, voice: { ...caps.voice, available: false, reason: 'Voice is off.' }, suggestions: [] });
    renderAssistant();
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/not available/));
    expect(box().disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', true);
    expect(screen.getByText(/Voice input is off/)).toBeTruthy();
  });

  it('shows a status message when the server reports an unavailable or limited answer', async () => {
    stub((u) => (u === '/api/assistant/chat' ? { body: reply({ status: 'limit_reached', answer: null, message: 'The assistant reached its step or time limit.', tools: [], sources: [] }) } : undefined));
    renderAssistant();
    await waitFor(() => screen.getByLabelText('Your question'));
    fireEvent.change(box(), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/step or time limit/)).toBeTruthy());
  });

  it('falls back gracefully when the browser cannot record', async () => {
    stub();
    vi.stubGlobal('MediaRecorder', undefined);
    renderAssistant();
    await waitFor(() => expect(screen.getByText(/not supported in this browser/)).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Speak/ })).toBeNull();
    expect(box().disabled).toBe(false);
  });

  it('explains a denied microphone and keeps typing available', async () => {
    stub();
    mic(() => Promise.reject(Object.assign(new Error('no'), { name: 'NotAllowedError' })));
    renderAssistant();
    await waitFor(() => screen.getByRole('button', { name: /Speak/ }));
    fireEvent.click(screen.getByRole('button', { name: /Speak/ }));
    await waitFor(() => expect(screen.getByText(/Microphone access was blocked/)).toBeTruthy());
    expect(box().disabled).toBe(false);
  });

  it('voice: records, shows an editable transcript, and sends it through the same chat endpoint', async () => {
    stub((u) => (u.startsWith('/api/assistant/transcribe')
      ? { body: { transcript: 'Which cameras are currently offline?', model: 'muse-voice-transcribe-1.0', correlation_id: 'v1', language: 'Hindi', audio_seconds: 2, est_cost_usd: 0.0001 } }
      : undefined));
    mic(() => Promise.resolve(stream));
    renderAssistant();
    await waitFor(() => screen.getByRole('button', { name: /Speak/ }));
    fireEvent.change(screen.getByLabelText(/Language/), { target: { value: 'Hindi' } });
    fireEvent.click(screen.getByRole('button', { name: /Speak/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Stop/ })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /Stop/ }));
    await waitFor(() => expect(box().value).toBe('Which cameras are currently offline?'));
    const tcall = calls.find((c) => c.url.startsWith('/api/assistant/transcribe'))!;
    expect(tcall.url).toContain('language=Hindi');
    expect((tcall.init?.headers as Record<string, string>)['Content-Type']).toBe('audio/wav');
    expect(calls.filter((c) => c.url === '/api/assistant/chat').length).toBe(0);   // not auto-sent
    expect(screen.getByText(/Check the text/)).toBeTruthy();
    // the user corrects the transcript before submitting
    fireEvent.change(box(), { target: { value: 'Which cameras are offline right now?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(chatBodies().length).toBe(1));
    expect(chatBodies()[0]).toEqual({ message: 'Which cameras are offline right now?', conversation_id: null, input_mode: 'voice' });
    expect(calls.filter((c) => c.url === '/api/assistant/chat')[0].url).toBe('/api/assistant/chat');   // same endpoint as typed
    await waitFor(() => expect(screen.getByText('voice')).toBeTruthy());
  });

  it('shows transcription failures without losing the ability to type', async () => {
    stub((u) => (u.startsWith('/api/assistant/transcribe') ? { status: 429, body: { detail: 'The transcription service is busy. Please try again shortly.' } } : undefined));
    mic(() => Promise.resolve(stream));
    renderAssistant();
    await waitFor(() => screen.getByRole('button', { name: /Speak/ }));
    fireEvent.click(screen.getByRole('button', { name: /Speak/ }));
    await waitFor(() => screen.getByRole('button', { name: /Stop/ }));
    fireEvent.click(screen.getByRole('button', { name: /Stop/ }));
    await waitFor(() => expect(screen.getByText(/transcription service is busy/)).toBeTruthy());
    expect(box().value).toBe('');
    fireEvent.change(box(), { target: { value: 'typed instead' } });
    expect(box().value).toBe('typed instead');
  });

  it('typed text after a voice draft is sent as text when the draft was replaced by a suggestion', async () => {
    stub();
    renderAssistant();
    await waitFor(() => screen.getByRole('button', { name: /offline/ }));
    fireEvent.click(screen.getByRole('button', { name: /offline/ }));
    expect(box().value).toBe('Which cameras are currently offline?');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(chatBodies()[0].input_mode).toBe('text'));
  });

  it('lets the user confirm or cancel a proposed action', async () => {
    stub((u) => {
      if (u === '/api/assistant/chat') return { body: reply({ pending_actions: [{ action_id: 'act1', summary: 'Move incident #3 (open) to acknowledged' }] }) };
      if (u === '/api/assistant/actions/act1/confirm') return { body: { ok: true, summary: 'Move incident #3 (open) to acknowledged' } };
      return undefined;
    });
    renderAssistant();
    await waitFor(() => screen.getByLabelText('Your question'));
    fireEvent.change(box(), { target: { value: 'acknowledge incident 3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/Needs your reply/)).toBeTruthy());
    expect(calls.some((c) => c.url.includes('/actions/'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Yes' }));
    await waitFor(() => expect(screen.getByText(/Done:/)).toBeTruthy());
  });

  it('offers Yes, No and Cancel for a permanent change and cancels everything on Cancel', async () => {
    stub((u) => {
      if (u === '/api/assistant/chat') return { body: reply({ pending_actions: [{ action_id: 'd1', summary: 'PERMANENTLY DELETE pandal X', destructive: true }] }) };
      if (u === '/api/assistant/actions/cancel-all') return { body: { ok: true, cancelled: 1 } };
      return undefined;
    });
    renderAssistant();
    await waitFor(() => screen.getByLabelText('Your question'));
    fireEvent.change(box(), { target: { value: 'delete pandal X' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/Permanent change, needs your reply/)).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Yes' }).className).toContain('danger');
    expect(screen.getByRole('button', { name: 'No' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.getByText('Cancelled')).toBeTruthy());
    expect(calls.some((c) => c.url.endsWith('/confirm'))).toBe(false);
  });
});
