import React from 'react';
import { Check, ChevronDown, Mic, MicOff, Send } from 'lucide-react';
import {
    isSecureVoiceContext,
    isSpeechRecognitionSupported,
    micErrorMessage,
    startListening,
    stopListening,
    stopSpeaking,
    subscribeListening,
    type SttProvider,
} from '../utils/speech';
import {
    isVoiceCallSupported,
    listMicrophones,
    type MicDevice,
    type VoiceCallState,
} from '../utils/voiceCall';

interface ChatbotFooterProps {
    newMessage: string;
    setNewMessage: (message: string) => void;
    handleSendMessage: () => void;
    /** Auto-send a final voice transcript (parent calls sendMessage). */
    onVoiceFinal?: (transcript: string) => void;
    placeholderMessage: string;
    botIsTyping: boolean;
    /** When true, STT is available, and the page is HTTPS or localhost, show the mic. */
    enableVoice?: boolean;
    /** Bot's BCP-47 voice language; falls back to the browser language. */
    voiceLanguage?: string | null;
    sttProvider?: SttProvider;
    /** Upload a recorded blob and return the transcript (Deepgram path). */
    transcribeAudio?: (blob: Blob) => Promise<string>;
    /** The bot offers a realtime voice call. */
    voiceCall?: boolean;
    /** Set while a call is running. */
    callState?: VoiceCallState | null;
    callMuted?: boolean;
    /** Live caption of what the visitor is saying. */
    callCaption?: string;
    /** Why the last call ended, when it ended badly. */
    callError?: string | null;
    onStartCall?: () => void;
    onEndCall?: () => void;
    onToggleCallMute?: () => void;
    /** Typed messages during a call go into the call. False if it couldn't. */
    onCallSendText?: (text: string) => boolean;
    /** Live loudness (0..1) of the visitor's mic and the bot's voice. */
    getCallLevels?: () => { mic: number; bot: number };
    getCallMicrophone?: () => string | null;
    onSelectCallMicrophone?: (deviceId: string) => void;
}

const CALL_STATUS: Record<VoiceCallState, string> = {
    connecting: 'Connecting',
    listening: 'Listening',
    speaking: 'Speaking',
};

const BAR_COUNT = 6;
/** Per-bar weight so the meter reads as a voice, not a block. */
const BAR_SHAPE = [0.6, 1, 0.75, 0.9, 0.65, 0.8];
const DOT_COUNT = 5;

const prefersReducedMotion = (): boolean =>
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Drives the call's level meters from audio, outside React renders. */
const useCallMeters = (
    active: boolean,
    speaking: boolean,
    getLevels: (() => { mic: number; bot: number }) | undefined,
    barsRef: React.RefObject<HTMLSpanElement>,
    dotsRef: React.RefObject<HTMLSpanElement>,
) => {
    const speakingRef = React.useRef(speaking);
    speakingRef.current = speaking;
    React.useEffect(() => {
        if (!active || !getLevels || prefersReducedMotion()) return undefined;
        let frame = 0;
        let voice = 0;
        let mic = 0;
        const tick = () => {
            const levels = getLevels();
            mic += (levels.mic - mic) * 0.3;
            voice += ((speakingRef.current ? levels.bot : levels.mic) - voice) * 0.25;
            const t = performance.now() / 150;
            const bars = barsRef.current?.children;
            if (bars) {
                for (let i = 0; i < bars.length; i += 1) {
                    const wobble = 0.7 + 0.3 * Math.sin(t + i * 1.7);
                    const h = 0.25 + Math.min(1, voice * BAR_SHAPE[i] * wobble * 1.7) * 0.75;
                    (bars[i] as HTMLElement).style.transform = `scaleY(${h.toFixed(3)})`;
                }
            }
            const dots = dotsRef.current?.children;
            if (dots) {
                const lit = Math.round(Math.min(1, mic * 1.6) * DOT_COUNT);
                for (let i = 0; i < dots.length; i += 1) {
                    (dots[i] as HTMLElement).dataset.on = i < lit ? '1' : '0';
                }
            }
            frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
        return () => {
            cancelAnimationFrame(frame);
            barsRef.current?.childNodes.forEach((bar) => {
                (bar as HTMLElement).style.transform = '';
            });
            dotsRef.current?.childNodes.forEach((dot) => {
                (dot as HTMLElement).dataset.on = '0';
            });
        };
    }, [active, getLevels, barsRef, dotsRef]);
};

// `pointer: coarse` is true on touch devices (phones, most tablets).
// Captured once at module scope — the practical effect of orientation
// changes is negligible and not worth a media-query listener.
const isCoarsePointer =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(pointer: coarse)').matches;

function pickRecorderMime(): string | undefined {
    if (typeof MediaRecorder === 'undefined') return undefined;
    const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg',
    ];
    return candidates.find((t) => MediaRecorder.isTypeSupported(t));
}

const ChatbotFooter: React.FC<ChatbotFooterProps> = ({
    newMessage,
    setNewMessage,
    handleSendMessage,
    onVoiceFinal,
    placeholderMessage,
    botIsTyping,
    enableVoice,
    voiceLanguage,
    sttProvider = 'browser',
    transcribeAudio,
    voiceCall,
    callState,
    callMuted,
    callCaption,
    callError,
    onStartCall,
    onEndCall,
    onToggleCallMute,
    onCallSendText,
    getCallLevels,
    getCallMicrophone,
    onSelectCallMicrophone,
}) => {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const mediaRecorderRef = React.useRef<MediaRecorder | null>(null);
    const chunksRef = React.useRef<BlobPart[]>([]);
    const streamRef = React.useRef<MediaStream | null>(null);
    // Browser recognition state is owned by `speech.ts`; Deepgram
    // recording state is owned here.
    const [browserListening, setBrowserListening] = React.useState(false);
    const [recording, setRecording] = React.useState(false);
    const [transcribing, setTranscribing] = React.useState(false);
    const [micError, setMicError] = React.useState<string | null>(null);
    const listening = browserListening || recording;

    const deepgramMic =
        sttProvider === 'deepgram' &&
        typeof navigator !== 'undefined' &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof MediaRecorder !== 'undefined' &&
        typeof transcribeAudio === 'function';
    const browserMic = sttProvider !== 'deepgram' && isSpeechRecognitionSupported();
    const showMic = Boolean(enableVoice) && (deepgramMic || browserMic) && isSecureVoiceContext();
    const busy = botIsTyping || transcribing;

    React.useEffect(() => subscribeListening(setBrowserListening), []);

    // Auto-focus when the bot finishes typing — but never on touch
    // devices, where focusing the textarea pops the on-screen keyboard
    // and obscures the reply the user just received.
    React.useEffect(() => {
        if (!botIsTyping && !isCoarsePointer) {
            textareaRef.current?.focus();
        }
    }, [botIsTyping]);

    const stopDeepgramRecording = React.useCallback(() => {
        const recorder = mediaRecorderRef.current;
        if (recorder && recorder.state !== 'inactive') {
            try {
                recorder.stop();
            } catch {
                // ignore
            }
        }
        mediaRecorderRef.current = null;
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((t) => t.stop());
            streamRef.current = null;
        }
        setRecording(false);
    }, []);

    const stopMic = React.useCallback(() => {
        if (sttProvider === 'deepgram') {
            stopDeepgramRecording();
        } else {
            stopListening();
        }
    }, [sttProvider, stopDeepgramRecording]);

    React.useEffect(() => {
        if (busy && listening) {
            stopMic();
        }
    }, [busy, listening, stopMic]);

    React.useEffect(() => {
        return () => {
            stopListening();
            stopDeepgramRecording();
        };
    }, [stopDeepgramRecording]);

    const sendAndMaybeBlur = () => {
        if (newMessage?.trim() === '') return;
        if (callState) {
            // In a call, typing is part of the conversation.
            if (onCallSendText?.(newMessage)) setNewMessage('');
            return;
        }
        setMicError(null);
        if (listening) {
            stopMic();
        }
        handleSendMessage?.();
        // On phones, dismiss the keyboard after send so the reply is
        // visible. Desktop keeps focus to support rapid follow-ups.
        if (isCoarsePointer) {
            textareaRef.current?.blur();
        }
    };

    const startDeepgramMic = async (withDraft: (transcript: string) => string) => {
        if (!transcribeAudio) return;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;
            const mime = pickRecorderMime();
            const recorder = mime
                ? new MediaRecorder(stream, { mimeType: mime })
                : new MediaRecorder(stream);
            mediaRecorderRef.current = recorder;
            chunksRef.current = [];
            recorder.ondataavailable = (ev) => {
                if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
            };
            recorder.onstop = async () => {
                const blob = new Blob(chunksRef.current, {
                    type: recorder.mimeType || 'audio/webm',
                });
                chunksRef.current = [];
                if (streamRef.current) {
                    streamRef.current.getTracks().forEach((t) => t.stop());
                    streamRef.current = null;
                }
                setRecording(false);
                if (!blob.size) return;
                setTranscribing(true);
                try {
                    const transcript = (await transcribeAudio(blob)).trim();
                    if (transcript) {
                        const combined = withDraft(transcript);
                        setNewMessage(combined);
                        onVoiceFinal?.(combined);
                    }
                } catch (err) {
                    console.warn('Deepgram STT failed', err);
                    setMicError(micErrorMessage('network'));
                } finally {
                    setTranscribing(false);
                }
            };
            recorder.start();
            setRecording(true);
        } catch (err) {
            console.warn('Microphone permission / recorder failed', err);
            const name = (err as { name?: string } | null)?.name;
            setMicError(
                micErrorMessage(
                    name === 'NotAllowedError'
                        ? 'not-allowed'
                        : name === 'NotFoundError'
                            ? 'audio-capture'
                            : 'start-failed',
                ),
            );
            stopDeepgramRecording();
        }
    };

    const toggleMic = () => {
        if (busy) return;
        if (listening) {
            stopMic();
            return;
        }
        stopSpeaking();
        setMicError(null);
        // Speech is added after whatever the visitor already typed.
        const draft = (newMessage ?? '').trim();
        const withDraft = (transcript: string) =>
            draft ? `${draft} ${transcript}` : transcript;
        if (sttProvider === 'deepgram') {
            void startDeepgramMic(withDraft);
            return;
        }
        startListening({
            lang: voiceLanguage,
            onInterim: (transcript) => setNewMessage(withDraft(transcript)),
            onFinal: (transcript) => {
                const combined = withDraft(transcript);
                setNewMessage(combined);
                if (transcript.trim()) {
                    onVoiceFinal?.(combined);
                }
            },
            onError: (code) => setMicError(micErrorMessage(code)),
        });
    };

    const inCall = Boolean(callState);
    const placeholder = inCall
        ? callCaption || (callState === 'connecting' ? 'Connecting…' : placeholderMessage)
        : transcribing
            ? 'Transcribing…'
            : listening
                ? (sttProvider === 'deepgram' ? 'Recording… tap mic to stop' : 'Listening…')
                : placeholderMessage;

    const showCall = Boolean(voiceCall) && Boolean(onStartCall) && isVoiceCallSupported();
    const shownError = micError || callError || null;

    const barsRef = React.useRef<HTMLSpanElement>(null);
    const dotsRef = React.useRef<HTMLSpanElement>(null);
    useCallMeters(inCall && callState !== 'connecting', callState === 'speaking', getCallLevels, barsRef, dotsRef);

    // Microphone picker (chevron), during a call.
    const [micMenu, setMicMenu] = React.useState<MicDevice[] | null>(null);
    const micMenuRef = React.useRef<HTMLDivElement>(null);
    const openMicMenu = async () => {
        if (micMenu) {
            setMicMenu(null);
            return;
        }
        setMicMenu(await listMicrophones());
    };
    React.useEffect(() => {
        if (!micMenu) return undefined;
        const close = (e: MouseEvent | KeyboardEvent) => {
            if (e instanceof KeyboardEvent ? e.key === 'Escape' : !micMenuRef.current?.contains(e.target as Node)) {
                setMicMenu(null);
            }
        };
        document.addEventListener('mousedown', close);
        document.addEventListener('keydown', close);
        return () => {
            document.removeEventListener('mousedown', close);
            document.removeEventListener('keydown', close);
        };
    }, [micMenu]);
    React.useEffect(() => {
        if (!inCall) setMicMenu(null);
    }, [inCall]);

    const callStatus = callState
        ? callMuted && callState !== 'speaking'
            ? 'Muted'
            : CALL_STATUS[callState as VoiceCallState]
        : '';
    const currentMic = micMenu ? getCallMicrophone?.() ?? null : null;

    return (
        <div className="footer">
            {inCall ? (
                <div className={`emw-callrow emw-callrow--${callState}`}>
                    <span className="emw-callrow__bars" ref={barsRef} title={callStatus} aria-hidden="true">
                        {Array.from({ length: BAR_COUNT }, (_, i) => <span key={i} />)}
                    </span>
                    <span className="emw-callrow__status" role="status" aria-live="polite">
                        {callStatus}
                    </span>
                    <button type="button" className="emw-callrow__end" onClick={onEndCall} aria-label="End voice call">
                        End
                    </button>
                </div>
            ) : null}
            <div className="input-container">
                <textarea
                    ref={textareaRef}
                    placeholder={placeholder}
                    value={newMessage}
                    onChange={(e) => {
                        setNewMessage(e.target.value);
                        if (micError) setMicError(null);
                    }}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendAndMaybeBlur();
                        }
                    }}
                    rows={1}
                    disabled={busy || callState === 'connecting'}
                    autoFocus={!isCoarsePointer}
                    className={callCaption ? 'emw-has-caption' : undefined}
                />
                {showMic && !inCall && (
                    <button
                        type="button"
                        className={`mic-icon ${listening ? 'mic-icon--active' : ''} ${busy ? 'mic-icon--disabled' : ''}`}
                        aria-label={listening ? 'Stop listening' : 'Speak message'}
                        aria-pressed={listening}
                        disabled={busy}
                        onClick={busy ? undefined : toggleMic}
                    >
                        {listening ? (
                            <MicOff size={18} aria-hidden="true" />
                        ) : (
                            <Mic size={18} aria-hidden="true" />
                        )}
                    </button>
                )}
                <button
                    type="button"
                    className={`send-icon ${newMessage?.trim() !== '' ? '' : 'send-icon--disabled'}`}
                    aria-label="Send"
                    disabled={newMessage?.trim() === ''}
                    onClick={newMessage?.trim() !== '' ? sendAndMaybeBlur : undefined}
                >
                    <Send size={18} aria-hidden="true" />
                </button>
                {(showCall || inCall) && (
                    <div className={`emw-callseg${inCall ? ' emw-callseg--live' : ''} emw-callseg--${callState || 'idle'}`}>
                        <button
                            type="button"
                            className={`emw-callseg__mic${callMuted ? ' emw-callseg__mic--muted' : ''}`}
                            disabled={(!inCall && busy) || callState === 'connecting'}
                            aria-label={
                                !inCall ? 'Start voice call' : callMuted ? 'Unmute microphone' : 'Mute microphone'
                            }
                            aria-pressed={inCall ? Boolean(callMuted) : undefined}
                            title={!inCall ? 'Start voice call' : callMuted ? 'Unmute' : 'Mute'}
                            onClick={() => {
                                if (inCall) {
                                    onToggleCallMute?.();
                                    return;
                                }
                                stopMic();
                                setMicError(null);
                                onStartCall?.();
                            }}
                        >
                            {callMuted ? <MicOff size={18} aria-hidden="true" /> : <Mic size={18} aria-hidden="true" />}
                            <span className="emw-callseg__dots" ref={dotsRef} aria-hidden="true">
                                {Array.from({ length: DOT_COUNT }, (_, i) => <span key={i} data-on="0" />)}
                            </span>
                        </button>
                        {inCall && (
                            <div className="emw-callseg__menu-wrap" ref={micMenuRef}>
                                <button
                                    type="button"
                                    className="emw-callseg__chevron"
                                    aria-label="Choose microphone"
                                    aria-haspopup="listbox"
                                    aria-expanded={Boolean(micMenu)}
                                    onClick={() => void openMicMenu()}
                                    disabled={callState === 'connecting'}
                                >
                                    <ChevronDown size={16} aria-hidden="true" />
                                </button>
                                {micMenu && (
                                    <div className="emw-callseg__menu" role="listbox" aria-label="Microphone">
                                        <div className="emw-callseg__menu-title">Microphone</div>
                                        {micMenu.length === 0 ? (
                                            <div className="emw-callseg__menu-empty">No microphones found</div>
                                        ) : (
                                            micMenu.map((device) => (
                                                <button
                                                    type="button"
                                                    key={device.id}
                                                    role="option"
                                                    aria-selected={device.id === currentMic}
                                                    className="emw-callseg__menu-item"
                                                    onClick={() => {
                                                        onSelectCallMicrophone?.(device.id);
                                                        setMicMenu(null);
                                                    }}
                                                >
                                                    <span>{device.label}</span>
                                                    {device.id === currentMic ? <Check size={14} aria-hidden="true" /> : null}
                                                </button>
                                            ))
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
            {shownError ? (
                <p className="emw-mic-error" role="status">{shownError}</p>
            ) : null}
        </div>
    );
};

export default ChatbotFooter;
