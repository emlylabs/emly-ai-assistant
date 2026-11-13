import React from 'react';
import { Send } from 'lucide-react';

interface ChatbotFooterProps {
    newMessage: string;
    setNewMessage: (message: string) => void;
    handleSendMessage: () => void;
    placeholderMessage: string;
    botIsTyping: boolean;
}

// `pointer: coarse` is true on touch devices (phones, most tablets).
// Captured once at module scope — the practical effect of orientation
// changes is negligible and not worth a media-query listener.
const isCoarsePointer =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(pointer: coarse)').matches;

const ChatbotFooter: React.FC<ChatbotFooterProps> = ({ newMessage, setNewMessage, handleSendMessage, placeholderMessage, botIsTyping }) => {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);

    // Auto-focus when the bot finishes typing — but never on touch
    // devices, where focusing the textarea pops the on-screen keyboard
    // and obscures the reply the user just received.
    React.useEffect(() => {
        if (!botIsTyping && !isCoarsePointer) {
            textareaRef.current?.focus();
        }
    }, [botIsTyping]);

    const sendAndMaybeBlur = () => {
        if (newMessage?.trim() === '') return;
        handleSendMessage?.();
        // On phones, dismiss the keyboard after send so the reply is
        // visible. Desktop keeps focus to support rapid follow-ups.
        if (isCoarsePointer) {
            textareaRef.current?.blur();
        }
    };

    return (
        <div className="footer">
            <div className="input-container">
                <textarea
                    ref={textareaRef}
                    placeholder={placeholderMessage}
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
