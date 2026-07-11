import React from 'react';

interface DynamicDataRendererProps {
    data: unknown;
}

interface DataRow {
    [key: string]: unknown;
}

const isDynamicDataArray = (value: unknown): value is DataRow[] => {
    return Array.isArray(value) && value.length > 0 && typeof value[0] === 'object' && value[0] !== null;
};

const formatValue = (value: unknown): string => {
    if (value === null || value === undefined) {
        return 'N/A';
    }

    if (typeof value === 'string') {
        return value.length > 200 ? value.substring(0, 200) + '...' : value;
    }

    if (typeof value === 'boolean') {
        return value ? 'Yes' : 'No';
    }

    if (typeof value === 'number') {
        return String(value);
    }

    if (typeof value === 'object') {
        return JSON.stringify(value);
    }

    return String(value);
};

const extractAllKeys = (dataArray: DataRow[]): string[] => {
    const keysSet = new Set<string>();
    dataArray.forEach(row => {
        Object.keys(row).forEach(key => keysSet.add(key));
    });
    return Array.from(keysSet);
};

const isLongValue = (value: string): boolean => value.length > 100;

const DataTable: React.FC<{ rows: DataRow[]; keys: string[] }> = ({ rows, keys }) => {
    return (
        <div className="emw-data-table-container">
            <table className="emw-data-table">
                <thead>
                    <tr>
                        {keys.map(key => (
                            <th key={key} className="emw-data-header">
                                {key.replace(/_/g, ' ')}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className="emw-data-row">
                            {keys.map(key => {
                                const value = row[key];
                                const formattedValue = formatValue(value);
                                const isLong = isLongValue(formattedValue);
                                return (
                                    <td key={`${rowIdx}-${key}`} className={`emw-data-cell ${isLong ? 'emw-long-content' : ''}`} title={isLong ? formattedValue : undefined}>
                                        {formattedValue}
                                    </td>
                                );
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const DataCards: React.FC<{ rows: DataRow[]; keys: string[] }> = ({ rows, keys }) => {
    return (
        <div className="emw-data-cards">
            {rows.map((row, idx) => (
                <div key={idx} className="emw-data-card">
                    {keys.map(key => {
                        const value = row[key];
                        const formattedValue = formatValue(value);
                        return (
                            <div key={key} className="emw-data-field">
                                <div className="emw-field-label">{key.replace(/_/g, ' ')}:</div>
                                <div className="emw-field-value">{formattedValue}</div>
                            </div>
                        );
                    })}
                </div>
            ))}
        </div>
    );
};

const DynamicDataRenderer: React.FC<DynamicDataRendererProps> = ({ data }) => {
    if (!isDynamicDataArray(data)) {
        return null;
    }

    const keys = extractAllKeys(data);
    const useCards = keys.length > 5 || data.some(row => Object.values(row).some(val => isLongValue(formatValue(val))));

    return useCards ? <DataCards rows={data} keys={keys} /> : <DataTable rows={data} keys={keys} />;
};

export default DynamicDataRenderer;
