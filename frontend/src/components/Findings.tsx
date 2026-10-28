import type { Validation } from "@/lib/types";

export function ValidationBadge({ v }: { v: Validation }) {
  const errors = v.findings.filter((f) => f.level === "error").length;
  const warnings = v.findings.length - errors;
  if (errors) return <span className="chip bg-red-50 text-red-700">차단 {errors}</span>;
  if (warnings) return <span className="chip bg-amber-50 text-amber-700">확인 {warnings}</span>;
  return <span className="chip bg-emerald-50 text-emerald-700">검증 통과</span>;
}

export function FindingList({ v }: { v: Validation }) {
  if (!v.findings.length) return null;
  return (
    <ul className="space-y-1">
      {v.findings.map((f, i) => (
        <li
          key={i}
          className={`rounded-md px-2 py-1 text-xs leading-relaxed ${
            f.level === "error" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"
          }`}
        >
          <span className="mr-1 font-semibold">{f.level === "error" ? "차단" : "확인"}</span>
          {f.message}
        </li>
      ))}
    </ul>
  );
}
