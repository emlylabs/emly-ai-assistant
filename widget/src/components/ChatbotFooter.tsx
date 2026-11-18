import React from 'react';
import { Mic, MicOff, Send } from 'lucide-react';
import {
    isSpeechRecognitionSupported,
    startListening,
    stopListening,
    stopSpeaking,
} from '../utils/speech';

interface ChatbotFooterProps {
    newMessage: string;
    setNewMessage: (message: string) => void;
    handleSendMessage: () => void;
    /** Auto-send a final voice transcript (parent calls sendMessage). */
    onVoiceFinal?: (transcript: string) => void;
    placeholderMessage: string;
    botIsTyping: boolean;
    /** When true and recognition is supported, show the mic control. */
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
    const showMic = Boolean(enableVoice) && isSpeechRecognitionSupported();

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
            setListening(false);
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
            setListening(false);
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
            setListening(false);
            return;
        }
        stopSpeaking();
        const started = startListening({
            onInterim: (transcript) => setNewMessage(transcript),
            onFinal: (transcript) => {
                setNewMessage(transcript);
                setListening(false);
                if (transcript.trim()) {
                    onVoiceFinal?.(transcript);
                }
            },
            onEnd: () => setListening(false),
            onError: () => setListening(false),
        });
        setListening(started);
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
        </div>
    );
};

export default ChatbotFooter;
