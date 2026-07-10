import React from 'react';
import { Send } from 'lucide-react';

interface ChatbotFooterProps {
    newMessage: string;
    setNewMessage: (message: string) => void;
    handleSendMessage: () => void;
    placeholderMessage: string;
    botIsTyping: boolean;
}

const ChatbotFooter: React.FC<ChatbotFooterProps> = ({ newMessage, setNewMessage, handleSendMessage, placeholderMessage, botIsTyping }) => {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);

    // Auto-focus the input when the bot finishes typing or on mount
    React.useEffect(() => {
        if (!botIsTyping) {
            textareaRef.current?.focus();
        }
    }, [botIsTyping]);

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
                            handleSendMessage?.();
                        }
                    }}
                    rows={1}
                    disabled={botIsTyping}
                    autoFocus
                />
                <button
                    type="button"
                    className={`send-icon ${newMessage?.trim() !== '' ? '' : 'send-icon--disabled'}`}
                    aria-label="Send"
                    disabled={newMessage?.trim() === ''}
                    onClick={newMessage?.trim() !== '' ? handleSendMessage : undefined}
                >
                    <Send size={18} aria-hidden="true" />
                </button>
            </div>
        </div>
    );
};

export default ChatbotFooter;
