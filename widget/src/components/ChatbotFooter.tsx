import React from 'react';
import { Mic, MicOff, Send } from 'lucide-react';
import {
    isSecureVoiceContext,
    isSpeechRecognitionSupported,
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
        startListening({
            onInterim: (transcript) => setNewMessage(transcript),
            onFinal: (transcript) => {
                setNewMessage(transcript);
                if (transcript.trim()) {
                    onVoiceFinal?.(transcript);
                }
            },
            onError: (code) => {
                if (code === 'not-allowed') {
                    setMicError('Microphone permission was blocked.');
                } else if (code === 'audio-capture') {
                    setMicError('No microphone was found.');
                } else if (code === 'network') {
                    setMicError('Voice input needs a network connection.');
                }
            },
        });
    };

    return (
        <div className="footer">
            <div className="input-container">
                <textarea
                    ref={textareaRef}
                    placeholder={listening ? 'Listening…' : placeholderMessage}
                    value={newMessage}
                    onChange={(e) => setNewMessage(e.target.value)}
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
