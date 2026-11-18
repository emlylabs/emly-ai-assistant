import React from 'react';
import { Mic, MicOff, Send } from 'lucide-react';
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
}

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

    const placeholder = transcribing
        ? 'Transcribing…'
        : listening
            ? (sttProvider === 'deepgram' ? 'Recording… tap mic to stop' : 'Listening…')
            : placeholderMessage;

    return (
        <div className="footer">
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
                    disabled={busy}
                    autoFocus={!isCoarsePointer}
                />
                {showMic && (
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
            </div>
            {micError ? (
                <p className="emw-mic-error" role="status">{micError}</p>
            ) : null}
        </div>
    );
};

export default ChatbotFooter;
