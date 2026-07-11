import React, { useRef, useEffect } from 'react';
import UserMessage from './UserMessage';
import BotMessage from './BotMessage';
import FormBubble from './forms/FormBubble';
import type { Message } from '../utils/sessionManager';

export interface ActionChip {
    key: string;
    label: string;
    onClick: () => void;
}

const URL_RE = /(https?:\/\/[^\s)]+)/;

interface ChatbotBodyProps {
    messages: Message[];
    chatIcon?: string;
    botIsTyping: boolean;
    openLinkInSameTab?: boolean;
    /** Submits a form bubble's values to `/widget/{bot}/action`. The
     * widget-level handler resolves the URL and posts; the body just
     * passes the message id back so the right bubble flips state. */
    onFormSubmit?: (messageId: string, values: Record<string, unknown>) => Promise<void>;
    onFormCancel?: (messageId: string) => void;
    /** Yes/No confirmation choice for triggers with `user_confirmation`. */
    onFormConfirm?: (messageId: string, choice: 'accepted' | 'declined') => void;
    /** Click handler for the post-limit engagement chip. */
    onPostLimitEngage?: (query: string) => void;
    /** OTP verify — resolves to error string or null on success. */
    onVerifyOtp?: (messageId: string, otp: string) => Promise<string | null>;
    /** OTP resend — resolves to error string or null on success. */
    onResendOtp?: (messageId: string) => Promise<string | null>;
    /** Chips rendered under bot messages whose text contains a URL. */
    linkActions?: ActionChip[];
    /** Chips rendered under bot messages whose `data` payload is non-null. */
    cardActions?: ActionChip[];
    /** Mirrors the bot's `feedback` config flag — gates thumbs up/down
     * footer under each rated assistant message. */
    showFeedback?: boolean;
    /** Active chat session id (used in the rate request body). */
    sessionId?: string | null;
    /** Persistent visitor id (used in the rate request body). */
    userId?: string;
    /** Mirrors the bot's `show_citations` config flag — gates RAG
     * citation pills under each assistant message. */
    showCitations?: boolean;
}

const ChatbotBody: React.FC<ChatbotBodyProps> = ({
    messages,
    botIsTyping,
    chatIcon,
    openLinkInSameTab,
    onFormSubmit,
    onFormCancel,
    onFormConfirm,
    onPostLimitEngage,
    onVerifyOtp,
    onResendOtp,
    linkActions,
    cardActions,
    showFeedback,
    sessionId,
    userId,
    showCitations,
}) => {
    const messagesContainerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (messagesContainerRef.current && messages.length > 0) {
            // Find the index of the last user message
            const lastUserMessageIndex = messages.length - 1 - [...messages].reverse().findIndex(msg => msg.isUserMessage);

            // Scroll to show the start of the bot's answer (just after the user message)
            if (lastUserMessageIndex >= 0 && lastUserMessageIndex < messages.length - 1) {
                const messagesElements = messagesContainerRef.current.querySelectorAll('.emw-message');
                if (messagesElements[lastUserMessageIndex + 1]) {
                    messagesElements[lastUserMessageIndex + 1].scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            } else {
                // If no user message, scroll to bottom
                messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
            }
        }
    }, [messages]);

    return (
        <div className="body" ref={messagesContainerRef}>
            <div className="emw-messages">
                {messages.map((message, index) => {
                    if (message.isUserMessage) {
                        return (
                            <UserMessage
                                key={index}
                                text={message.text}
                                timestamp={message.timestamp}
                            />
                        );
                    }
                    if (message.form) {
                        return (
                            <FormBubble
                                key={index}
                                form={message.form}
                                chatIcon={chatIcon}
                                onSubmit={values => onFormSubmit?.(message.id, values) ?? Promise.resolve()}
                                onCancel={() => onFormCancel?.(message.id)}
                                onConfirmationChoice={choice => onFormConfirm?.(message.id, choice)}
                                onPostLimitEngage={onPostLimitEngage}
                                onVerifyOtp={onVerifyOtp ? (otp) => onVerifyOtp(message.id, otp) : undefined}
                                onResendOtp={onResendOtp ? () => onResendOtp(message.id) : undefined}
                            />
                        );
                    }
                    const showLinkChips = (linkActions?.length ?? 0) > 0 && URL_RE.test(message.text);
                    const showCardChips = (cardActions?.length ?? 0) > 0 && message.data != null;
                    return (
                        <React.Fragment key={index}>
                            <BotMessage
                                text={message.text}
                                timestamp={message.timestamp}
                                chatIcon={chatIcon}
                                data={message.data}
                                openLinkInSameTab={openLinkInSameTab}
                                messageId={message.messageId}
                                showFeedback={showFeedback}
                                sessionId={sessionId}
                                userId={userId}
                                citations={message.citations}
                                showCitations={showCitations}
                            />
                            {(showLinkChips || showCardChips) && (
                                <ul className="emw-action-chips" role="list">
                                    {showLinkChips && linkActions!.map(chip => (
                                        <li key={`l-${chip.key}`}>
                                            <button type="button" onClick={chip.onClick}>{chip.label}</button>
                                        </li>
                                    ))}
                                    {showCardChips && cardActions!.map(chip => (
                                        <li key={`c-${chip.key}`}>
                                            <button type="button" onClick={chip.onClick}>{chip.label}</button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </React.Fragment>
                    );
                })}
                {botIsTyping && (
                    <BotMessage
                        text=""
                        timestamp=""
                        chatIcon={chatIcon}
                        isLoading={true}
                    />
                )}
            </div>
        </div>
    );
};

export default ChatbotBody;
