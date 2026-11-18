import React from 'react';
import { Bot, Volume2, VolumeX } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DynamicDataRenderer from './DynamicDataRenderer';
import MessageFeedback from './MessageFeedback';
import CitationPills from './CitationPills';
import type { Citation } from '../utils/sessionManager';
import {
    isSpeechSynthesisSupported,
    speak,
    stopSpeaking,
    subscribeSpeechProgress,
    type SpeechProgress,
} from '../utils/speech';

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
    /** Client-side bubble id — used to highlight the message being spoken. */
    messageKey?: string;
    /** Mirrors the bot's `feedback` config flag. */
    showFeedback?: boolean;
    sessionId?: string | null;
    userId?: string;
    /** RAG citations attached to the assistant reply. */
    citations?: Citation[];
    /** Mirrors the bot's `show_citations` config flag. */
    showCitations?: boolean;
    /** Mirrors the bot's `enable_voice` config flag. */
    enableVoice?: boolean;
}

const BotMessage: React.FC<BotMessageProps> = ({
    text,
    timestamp,
    chatIcon,
    isLoading,
    data,
    openLinkInSameTab,
    messageId,
    messageKey,
    showFeedback,
    sessionId,
    userId,
    citations,
    showCitations,
    enableVoice,
}) => {
    // Renders bot replies as markdown (GFM). Mirrors the
    // your-assistant/src/Utility/MarkdownRenderer.js pattern but stays
    // minimal — no math rendering (skip remark-math/rehype-katex to
    // keep the bundle small) and no link-card decoration.
    const [speech, setSpeech] = React.useState<SpeechProgress>({
        speakId: null,
        plainText: '',
        charIndex: 0,
        charLength: 0,
        speaking: false,
    });

    React.useEffect(() => subscribeSpeechProgress(setSpeech), []);

    const speakingThis =
        Boolean(messageKey) &&
        speech.speaking &&
        speech.speakId === messageKey;

    const showSpeak =
        Boolean(enableVoice) &&
        isSpeechSynthesisSupported() &&
        !isLoading &&
        Boolean(text?.trim());

    const toggleSpeak = () => {
        if (speakingThis) {
            stopSpeaking();
            return;
        }
        speak(text, { speakId: messageKey });
    };

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
        // S4: never load an image from an answer. A document or crawled page
        // can tell the model to add one whose URL carries the conversation,
        // and it would load in the visitor's browser with no click. Show its
        // alt text only.
        img: ({ alt }: { alt?: string }) => (alt ? <span>[{alt}]</span> : null),
    };

    const highlightStart = Math.max(0, speech.charIndex);
    const highlightEnd = Math.min(
        speech.plainText.length,
        highlightStart + Math.max(speech.charLength, 1),
    );

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
                            <div className={`emw-markdown ${speakingThis ? 'emw-markdown--speaking' : ''}`}>
                                {speakingThis ? (
                                    <p className="emw-tts-text" aria-live="polite">
                                        <span className="emw-tts-read">
                                            {speech.plainText.slice(0, highlightStart)}
                                        </span>
                                        <mark className="emw-tts-current">
                                            {speech.plainText.slice(highlightStart, highlightEnd)}
                                        </mark>
                                        <span className="emw-tts-upcoming">
                                            {speech.plainText.slice(highlightEnd)}
                                        </span>
                                    </p>
                                ) : (
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                        components={markdownComponents}
                                    >
                                        {text}
                                    </ReactMarkdown>
                                )}
                            </div>
                            <DynamicDataRenderer data={data} />
                        </>
                    )}
                </div>
                {!isLoading && showCitations && citations && citations.length > 0 && (
                    <CitationPills citations={citations} />
                )}
                {!isLoading && (
                    <div className="emw-bot-meta">
                        {timestamp ? (
                            <div className="emw-timestamp emw-bot-timestamp">{timestamp}</div>
                        ) : null}
                        {showSpeak && (
                            <button
                                type="button"
                                className={`emw-speak-btn ${speakingThis ? 'emw-speak-btn--active' : ''}`}
                                aria-label={speakingThis ? 'Stop speaking' : 'Speak reply'}
                                aria-pressed={speakingThis}
                                onClick={toggleSpeak}
                            >
                                {speakingThis ? (
                                    <VolumeX size={14} aria-hidden="true" />
                                ) : (
                                    <Volume2 size={14} aria-hidden="true" />
                                )}
                            </button>
                        )}
                    </div>
                )}
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
