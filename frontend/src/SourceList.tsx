export type Source = {
  document_id: string;
  chunk_id: string;
  marker: string;
  filename: string;
  page_number: number;
  quote: string;
  unavailable: boolean;
};

export default function SourceList({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  return (
    <ul className="source-list">
      {sources.map((source) => (
        <li key={source.chunk_id}>
          {source.unavailable ? (
            <span>[{source.marker}] {source.filename}, page {source.page_number} (deleted)</span>
          ) : (
            <a href={"/api/documents/" + source.document_id + "/file#page=" + source.page_number}
              target="_blank" rel="noreferrer">
              [{source.marker}] {source.filename}, page {source.page_number}
            </a>
          )}
          {!source.unavailable && source.quote ? <small>{source.quote}</small> : null}
        </li>
      ))}
    </ul>
  );
}
