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
} from '../utils/speech';

interface ChatbotFooterProps {
    newMessage: string;
    setNewMessage: (message: string) => void;
    handleSendMessage: () => void;
    /** Auto-send a final voice transcript (parent calls sendMessage). */
    onVoiceFinal?: (transcript: string) => void;
    placeholderMessage: string;
    botIsTyping: boolean;
    /** When true, recognition is supported, and the page is HTTPS or localhost, show the mic. */
    enableVoice?: boolean;
    /** Bot's BCP-47 voice language; falls back to the browser language. */
    voiceLanguage?: string | null;
}

// `pointer: coarse` is true on touch devices (phones, most tablets).
// Captured once at module scope — the practical effect of orientation
// changes is negligible and not worth a media-query listener.
const isCoarsePointer =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(pointer: coarse)').matches;

const ChatbotFooter: React.FC<ChatbotFooterProps> = ({
    newMessage,
    setNewMessage,
    handleSendMessage,
    onVoiceFinal,
    placeholderMessage,
    botIsTyping,
    enableVoice,
    voiceLanguage,
}) => {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const [listening, setListening] = React.useState(false);
    const [micError, setMicError] = React.useState<string | null>(null);
    const showMic = Boolean(enableVoice) && isSpeechRecognitionSupported() && isSecureVoiceContext();

    React.useEffect(() => subscribeListening(setListening), []);

    // Auto-focus when the bot finishes typing — but never on touch
    // devices, where focusing the textarea pops the on-screen keyboard
    // and obscures the reply the user just received.
    React.useEffect(() => {
        if (!botIsTyping && !isCoarsePointer) {
            textareaRef.current?.focus();
        }
    }, [botIsTyping]);

    React.useEffect(() => {
        if (botIsTyping && listening) {
            stopListening();
        }
    }, [botIsTyping, listening]);

    React.useEffect(() => {
        return () => {
            stopListening();
        };
    }, []);

    const sendAndMaybeBlur = () => {
        if (newMessage?.trim() === '') return;
        setMicError(null);
        if (listening) {
            stopListening();
        }
        handleSendMessage?.();
        // On phones, dismiss the keyboard after send so the reply is
        // visible. Desktop keeps focus to support rapid follow-ups.
        if (isCoarsePointer) {
            textareaRef.current?.blur();
        }
    };

    const toggleMic = () => {
        if (botIsTyping) return;
        if (listening) {
            stopListening();
            return;
        }
        stopSpeaking();
        setMicError(null);
        // Speech is added after whatever the visitor already typed.
        const draft = (newMessage ?? '').trim();
        const withDraft = (transcript: string) =>
            draft ? `${draft} ${transcript}` : transcript;
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

    return (
        <div className="footer">
            <div className="input-container">
                <textarea
                    ref={textareaRef}
                    placeholder={listening ? 'Listening…' : placeholderMessage}
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
                    disabled={botIsTyping}
                    autoFocus={!isCoarsePointer}
                />
                {showMic && (
                    <button
                        type="button"
                        className={`mic-icon ${listening ? 'mic-icon--active' : ''} ${botIsTyping ? 'mic-icon--disabled' : ''}`}
                        aria-label={listening ? 'Stop listening' : 'Speak message'}
                        aria-pressed={listening}
                        disabled={botIsTyping}
                        onClick={botIsTyping ? undefined : toggleMic}
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
