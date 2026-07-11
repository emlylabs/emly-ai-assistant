import React, { useState } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { rateMessage } from '../embed';

interface MessageFeedbackProps {
    messageId: number;
    sessionId: string | null;
    userId: string;
}

const MessageFeedback: React.FC<MessageFeedbackProps> = ({ messageId, sessionId, userId }) => {
    const [rating, setRating] = useState<-1 | 0 | 1>(0);
    const [pending, setPending] = useState(false);

    const submit = async (next: -1 | 0 | 1) => {
        if (pending || next === rating || !sessionId) return;
        const previous = rating;
        setRating(next);
        setPending(true);
        try {
            await rateMessage(messageId, next, sessionId, userId);
        } catch (err) {
            console.error('rating failed:', err);
            setRating(previous);
        } finally {
            setPending(false);
        }
    };

    const onUp = () => submit(rating === 1 ? 0 : 1);
    const onDown = () => submit(rating === -1 ? 0 : -1);

    return (
        <ul className="emw-feedback" role="list">
            <li>
                <button
                    type="button"
                    className={`emw-feedback-btn ${rating === 1 ? 'is-active' : ''}`}
                    aria-label="Rate response helpful"
                    aria-pressed={rating === 1}
                    disabled={pending}
                    onClick={onUp}
                >
                    <ThumbsUp size={14} aria-hidden="true" />
                </button>
            </li>
            <li>
                <button
                    type="button"
                    className={`emw-feedback-btn ${rating === -1 ? 'is-active' : ''}`}
                    aria-label="Rate response unhelpful"
                    aria-pressed={rating === -1}
                    disabled={pending}
                    onClick={onDown}
                >
                    <ThumbsDown size={14} aria-hidden="true" />
                </button>
            </li>
        </ul>
    );
};

export default MessageFeedback;
