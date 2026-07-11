import React, { useState } from 'react';
import { ChevronDown, ExternalLink, FileText } from 'lucide-react';
import type { Citation } from '../utils/sessionManager';

interface CitationPillsProps {
    citations: Citation[];
    /** Truncate the pill label at this many chars (with an ellipsis). */
    titleMaxChars?: number;
    /** Cap the chunk excerpt shown in the expanded card. */
    chunkMaxChars?: number;
}

const DEFAULT_TITLE_CHARS = 28;
const DEFAULT_CHUNK_CHARS = 280;

const parseMaybeJson = (value: unknown): Record<string, unknown> | null => {
    if (!value) return null;
    if (typeof value === 'object') return value as Record<string, unknown>;
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    if (!trimmed || trimmed === '{}') return null;
    try {
        return JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
        return null;
    }
};

interface DerivedCitation {
    title: string;
    url: string | null;
    description: string | null;
    image: string | null;
    chunk: string | null;
    host: string | null;
}

const deriveCitation = (citation: Citation): DerivedCitation => {
    const meta = citation.metadata ?? {};
    const og =
        parseMaybeJson(citation.og)
        ?? parseMaybeJson(meta.og)
        ?? {};

    const url =
        (typeof meta.source_url === 'string' && meta.source_url) ||
        (typeof og.url === 'string' && og.url) ||
        (typeof meta.source === 'string' && meta.source) ||
        null;

    const title =
        (typeof og.title === 'string' && og.title) ||
        (typeof meta.title === 'string' && meta.title) ||
        (typeof url === 'string' && url) ||
        'Source';

    const description =
        (typeof og.description === 'string' && og.description) || null;

    const image =
        (typeof og.image === 'string' && og.image) ||
        (typeof meta.image === 'string' && meta.image) ||
        null;

    const chunk = typeof citation.chunk === 'string' ? citation.chunk : null;

    let host: string | null = null;
    if (url) {
        try {
            host = new URL(url).hostname.replace(/^www\./, '');
        } catch {
            // Sources can be local file paths (data uploads). The host
            // stays null for those — the pill just shows the title and
            // there's no external link.
        }
    }

    return { title, url, description, image, chunk, host };
};

const isHttp = (url: string | null): boolean =>
    typeof url === 'string' && /^https?:\/\//i.test(url);

const truncate = (s: string, max: number): string =>
    s.length <= max ? s : s.slice(0, Math.max(0, max - 1)).trimEnd() + '…';

const CitationPills: React.FC<CitationPillsProps> = ({
    citations,
    titleMaxChars = DEFAULT_TITLE_CHARS,
    chunkMaxChars = DEFAULT_CHUNK_CHARS,
}) => {
    const [expanded, setExpanded] = useState<number | null>(null);

    if (!Array.isArray(citations) || citations.length === 0) return null;

    const items = citations.map(deriveCitation);

    return (
        <div className="emw-citations">
            <ul className="emw-cite-pill-row" role="list">
                {items.map((it, i) => {
                    const labelFull = it.title;
                    const label = truncate(labelFull, titleMaxChars);
                    const linkable = isHttp(it.url);
                    const isOpen = expanded === i;
                    return (
                        <li key={i} className={`emw-cite-pill ${isOpen ? 'is-open' : ''}`}>
                            {linkable ? (
                                <a
                                    href={it.url ?? '#'}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="emw-cite-pill-link"
                                    title={labelFull}
                                >
                                    <FileText size={12} aria-hidden="true" className="emw-cite-pill-icon" />
                                    <span className="emw-cite-pill-label">{label}</span>
                                    <ExternalLink size={10} aria-hidden="true" className="emw-cite-pill-ext" />
                                </a>
                            ) : (
                                <span className="emw-cite-pill-link emw-cite-pill-link--inert" title={labelFull}>
                                    <FileText size={12} aria-hidden="true" className="emw-cite-pill-icon" />
                                    <span className="emw-cite-pill-label">{label}</span>
                                </span>
                            )}
                            <button
                                type="button"
                                className="emw-cite-pill-toggle"
                                aria-expanded={isOpen}
                                aria-label={isOpen ? 'Hide citation details' : 'Show citation details'}
                                onClick={() => setExpanded(isOpen ? null : i)}
                            >
                                <ChevronDown
                                    size={12}
                                    aria-hidden="true"
                                    className={`emw-cite-pill-chev ${isOpen ? 'is-open' : ''}`}
                                />
                            </button>
                        </li>
                    );
                })}
            </ul>
            {expanded !== null && items[expanded] && (
                <CitationCard
                    citation={items[expanded]}
                    chunkMaxChars={chunkMaxChars}
                    onClose={() => setExpanded(null)}
                />
            )}
        </div>
    );
};

interface CardProps {
    citation: DerivedCitation;
    chunkMaxChars: number;
    onClose: () => void;
}

const CitationCard: React.FC<CardProps> = ({ citation, chunkMaxChars }) => {
    const linkable = isHttp(citation.url);
    return (
        <div className="emw-cite-card" role="region" aria-label="Citation details">
            {citation.image && (
                <img
                    src={citation.image}
                    alt=""
                    className="emw-cite-card-image"
                    loading="lazy"
                />
            )}
            <div className="emw-cite-card-body">
                {linkable ? (
                    <a
                        href={citation.url ?? '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="emw-cite-card-title"
                    >
                        {citation.title}
                        <ExternalLink size={11} aria-hidden="true" />
                    </a>
                ) : (
                    <span className="emw-cite-card-title emw-cite-card-title--inert">
                        {citation.title}
                    </span>
                )}
                {citation.host && (
                    <span className="emw-cite-card-host">{citation.host}</span>
                )}
                {citation.description && (
                    <p className="emw-cite-card-desc">{citation.description}</p>
                )}
                {citation.chunk && (
                    <blockquote className="emw-cite-card-chunk">
                        {truncate(citation.chunk, chunkMaxChars)}
                    </blockquote>
                )}
            </div>
        </div>
    );
};

export default CitationPills;
