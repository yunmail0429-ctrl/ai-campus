"use client";
export function QueryState({
  pending,
  error,
  retry,
}: {
  pending?: boolean;
  error?: Error | null;
  retry?: () => void;
}) {
  if (pending)
    return (
      <p className="work-status" role="status">
        正在加载…
      </p>
    );
  if (error)
    return (
      <div className="work-status" role="alert">
        <p>{error.message}</p>
        {retry && (
          <button type="button" className="soft-button mt-3" onClick={retry}>
            重新加载
          </button>
        )}
      </div>
    );
  return null;
}
