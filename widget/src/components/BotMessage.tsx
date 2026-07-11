import React from 'react';
import { Bot } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DynamicDataRenderer from './DynamicDataRenderer';
import MessageFeedback from './MessageFeedback';
import CitationPills from './CitationPills';
import type { Citation } from '../utils/sessionManager';

interface BotMessageProps {
    text: string;
    timestamp: string;
    chatIcon?: string;
    isLoading?: boolean;
    data?: unknown;
    /** Mirrors the bot's `open_link_in_same_tab` config flag. When true,
     * markdown links open in the host tab; otherwise in a new tab. */
    openLinkInSameTab?: boolean;
    /** Backend assistant message id; required to show the rating buttons. */
    messageId?: number;
    /** Mirrors the bot's `feedback` config flag. */
    showFeedback?: boolean;
    sessionId?: string | null;
    userId?: string;
    /** RAG citations attached to the assistant reply. */
    citations?: Citation[];
    /** Mirrors the bot's `show_citations` config flag. */
    showCitations?: boolean;
}

const BotMessage: React.FC<BotMessageProps> = ({
    text,
    timestamp,
    chatIcon,
    isLoading,
    data,
    openLinkInSameTab,
    messageId,
    showFeedback,
    sessionId,
    userId,
    citations,
    showCitations,
}) => {
    // Renders bot replies as markdown (GFM). Mirrors the
    // your-assistant/src/Utility/MarkdownRenderer.js pattern but stays
    // minimal — no math rendering (skip remark-math/rehype-katex to
    // keep the bundle small) and no link-card decoration.
    const linkTarget = openLinkInSameTab ? undefined : '_blank';
    const linkRel = openLinkInSameTab ? undefined : 'noopener noreferrer';
    const markdownComponents = {
        a: ({ href, children, ...rest }: { href?: string; children?: React.ReactNode } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
            const isMail = href?.startsWith('mailto:');
            return (
                <a
                    {...rest}
                    href={href}
                    target={isMail ? undefined : linkTarget}
                    rel={isMail ? undefined : linkRel}
                >
                    {children}
                </a>
            );
        },
    };

    return (
        <div className="emw-message emw-bot">
            {chatIcon ? (
                <img src={chatIcon} alt="Bot Avatar" className="emw-avatar" />
            ) : (
                <span className="emw-avatar emw-avatar--icon" aria-hidden="true">
                    <Bot size={18} />
                </span>
            )}
            <div className="emw-content emw-bot-content">
                <div className={`emw-message-bubble emw-bot-bubble ${isLoading ? 'emw-loading' : ''}`}>
                    {isLoading ? (
                        <>
                            <div className="emw-loader-container">
                                <span className="emw-loader-dots"></span>
                                <span className="emw-loader-dots"></span>
                                <span className="emw-loader-dots"></span>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="emw-markdown">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    components={markdownComponents}
                                >
                                    {text}
                                </ReactMarkdown>
                            </div>
                            <DynamicDataRenderer data={data} />
                        </>
                    )}
                </div>
                {!isLoading && showCitations && citations && citations.length > 0 && (
                    <CitationPills citations={citations} />
                )}
                {!isLoading && <div className="emw-timestamp emw-bot-timestamp">{timestamp}</div>}
                {!isLoading && showFeedback && messageId !== undefined && userId && (
                    <MessageFeedback
                        messageId={messageId}
                        sessionId={sessionId ?? null}
                        userId={userId}
                    />
                )}
            </div>
        </div>
    );
};

export default BotMessage;
