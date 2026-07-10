export interface CandidateRecord {
    id?: string;
    title?: string;
    candidate_name?: string;
    email?: string | null;
    phone?: string | null;
    score?: number | null;
    key_recommendations?: string | null;
    is_shortlisted?: boolean;
    is_blacklisted?: boolean;
    status?: string | null;
    file_path?: string;
    created_on?: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null;

const parseScore = (value: unknown): number | null | undefined => {
    if (value === null || value === undefined) {
        return value as null | undefined;
    }

    if (typeof value === 'number') {
        return Number.isFinite(value) ? value : null;
    }

    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (trimmed === '') {
            return null;
        }

        const parsed = Number(trimmed);
        return Number.isFinite(parsed) ? parsed : null;
    }

    return null;
};

const normalizeCandidateRecord = (record: CandidateRecord): CandidateRecord => ({
    ...record,
    score: parseScore(record.score),
});

const recordIdentity = (record: CandidateRecord): string => {
    const idParts = [
        record.id,
        record.candidate_name,
        record.file_path,
        record.email,
        record.phone,
        record.title,
    ];

    const key = idParts
        .map(part => (part ?? '').toString().trim().toLowerCase())
        .filter(Boolean)
        .join('|');

    return key || JSON.stringify(record);
};

const mergeRecords = (base: CandidateRecord, incoming: CandidateRecord): CandidateRecord => {
    const merged: CandidateRecord = { ...base };

    for (const [key, value] of Object.entries(incoming)) {
        if (value !== null && value !== undefined && value !== '') {
            (merged as Record<string, unknown>)[key] = value;
        }
    }

    const baseScore = parseScore(base.score);
    const incomingScore = parseScore(incoming.score);

    if (incomingScore !== null && incomingScore !== undefined) {
        merged.score = incomingScore;
    } else {
        merged.score = baseScore;
    }

    merged.is_shortlisted = Boolean(base.is_shortlisted || incoming.is_shortlisted);
    merged.is_blacklisted = Boolean(base.is_blacklisted || incoming.is_blacklisted);

    return merged;
};

const dedupeCandidateRecords = (records: CandidateRecord[]): CandidateRecord[] => {
    const byId = new Map<string, CandidateRecord>();

    records.forEach((rawRecord) => {
        const record = normalizeCandidateRecord(rawRecord);
        const key = recordIdentity(record);
        const existing = byId.get(key);

        if (!existing) {
            byId.set(key, record);
            return;
        }

        byId.set(key, mergeRecords(existing, record));
    });

    return Array.from(byId.values());
};

export const isCandidateRecord = (value: unknown): value is CandidateRecord => {
    if (!isRecord(value)) {
        return false;
    }

    return (
        'title' in value ||
        'score' in value ||
        'is_shortlisted' in value ||
        'is_blacklisted' in value ||
        'candidate_name' in value
    );
};

export const isCandidateArray = (value: unknown): value is CandidateRecord[] =>
    Array.isArray(value) && value.every(isCandidateRecord);

export const extractCandidateArray = (payload: unknown): CandidateRecord[] | null => {
    const nestedKeys = ['data', 'results', 'items', 'candidates', 'records'];

    const visit = (value: unknown, depth: number): CandidateRecord[] | null => {
        if (depth > 10) {
            return null;
        }

        if (isCandidateArray(value)) {
            return dedupeCandidateRecords(value);
        }

        if (Array.isArray(value)) {
            for (const item of value) {
                const extracted = visit(item, depth + 1);
                if (extracted && extracted.length > 0) {
                    return extracted;
                }
            }

            return null;
        }

        if (!isRecord(value)) {
            return null;
        }

        for (const key of nestedKeys) {
            if (!(key in value)) {
                continue;
            }

            const extracted = visit(value[key], depth + 1);
            if (extracted && extracted.length > 0) {
                return extracted;
            }
        }

        if (isCandidateRecord(value)) {
            return dedupeCandidateRecords([value]);
        }

        for (const nested of Object.values(value)) {
            const extracted = visit(nested, depth + 1);
            if (extracted && extracted.length > 0) {
                return extracted;
            }
        }

        return null;
    };

    return visit(payload, 0);
};

export const buildCandidateSummary = (records: CandidateRecord[]): string => {
    const scored = records.filter(item => typeof item.score === 'number') as Array<CandidateRecord & { score: number }>;
    const avgScore = scored.length > 0
        ? (scored.reduce((sum, item) => sum + item.score, 0) / scored.length).toFixed(1)
        : 'N/A';

    return `Found ${records.length} candidate records. Average score: ${avgScore}. See grouped chart summary below.`;
};
