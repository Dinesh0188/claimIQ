"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import type { AskResponse } from "@/lib/types";
import { Spinner } from "@/components/ui/spinner";
import { Button } from "@/components/ui/button";
import { MessageSquareText, AlertTriangle } from "lucide-react";

const EXAMPLE_PROMPTS = [
  "total hospital write-off by month",
  "top 5 leaking items",
  "how many claims are missing documents",
];

export function AskView() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (q: string) => claimiqApi.ask(q),
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (err) => {
      setError((err as Error).message);
    },
  });

  const submit = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || mutation.isPending) return;
    setResult(null);
    mutation.mutate(trimmed);
  };

  const handleExample = (prompt: string) => {
    setQuestion(prompt);
    submit(prompt);
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Ask</h1>
        <p className="text-muted mt-1">
          Ask questions about your audited claims portfolio in plain English.
        </p>
      </div>

      {/* Question input */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
        className="card space-y-3"
      >
        <label htmlFor="ask-question" className="text-sm font-medium text-muted">
          Question
        </label>
        <textarea
          id="ask-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(question);
            }
          }}
          rows={3}
          placeholder="e.g. total hospital write-off by month"
          className="w-full bg-panel-3 border border-line rounded px-3 py-2 text-sm text-white placeholder:text-muted-2 focus:outline-none focus:border-accent"
        />
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-2">Try:</span>
            {EXAMPLE_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => handleExample(prompt)}
                className="text-xs px-2.5 py-1 rounded-full bg-panel-3 border border-line text-muted hover:text-white hover:border-line-2 transition-colors"
              >
                {prompt}
              </button>
            ))}
          </div>
          <Button
            type="submit"
            variant="primary"
            disabled={!question.trim() || mutation.isPending}
          >
            {mutation.isPending ? (
              <>
                <Spinner className="w-4 h-4" /> Asking...
              </>
            ) : (
              <>
                <MessageSquareText size={16} /> Ask
              </>
            )}
          </Button>
        </div>
      </form>

      {/* Error state */}
      {error && (
        <div className="flex items-start gap-3 p-3 bg-red-500/5 border border-red-500/20 rounded text-sm text-red-400">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <div>
            <strong>The question couldn&apos;t be answered — the query was rejected:</strong>{" "}
            <span className="text-muted">{error}</span>
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-4">
          <div className="card">
            <h2 className="text-sm font-semibold mb-2">Answer</h2>
            <p className="text-sm text-muted">{result.explanation}</p>
          </div>

          <details className="card group">
            <summary className="cursor-pointer text-sm font-medium select-none">
              Generated SQL
            </summary>
            <p className="text-xs text-muted-2 mt-2">
              Read-only SQL, scoped to your tenant.
            </p>
            <pre className="mt-3 p-3 bg-panel-3 border border-line rounded text-xs font-mono text-accent overflow-x-auto">
              {result.sql}
            </pre>
          </details>

          {result.columns.length > 0 ? (
            <div className="card overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-muted-2 text-left text-xs">
                    {result.columns.map((col) => (
                      <th key={col} className="py-2.5 px-3">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr
                      key={i}
                      className="border-b border-line/50 hover:bg-panel-3"
                    >
                      {result.columns.map((col) => (
                        <td key={col} className="py-2.5 px-3">
                          {String(row[col] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="card text-center py-8">
              <p className="text-muted">No rows returned.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
