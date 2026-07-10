import React from 'react';

interface UserMessageProps {
    text: string;
    timestamp: string;
}

const UserMessage: React.FC<UserMessageProps> = ({ text, timestamp }) => {
    return (
        <div className="emw-message emw-user">
            <div className="emw-content emw-user-content">
                <div className="emw-message-bubble emw-user-bubble">
                    {text}
                </div>
                <div className="emw-timestamp emw-user-timestamp">{timestamp}</div>
            </div>
        </div>
    );
};

export default UserMessage;
