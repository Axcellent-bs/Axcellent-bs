import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * 화면 한 조각이 렌더 중 에러를 던져도 페이지 전체가 사라지지 않게 막는다.
 * 서버 응답에서 필드가 빠졌을 때(계약 변경) 흰 화면이 되는 걸 막는 용도.
 */
interface Props { children: ReactNode; title?: string }
interface State { message: string | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: Error): State {
    return { message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.message == null) return this.props.children;
    return (
      <div className="rounded-2xl bg-surface-2 p-4">
        <h3 className="text-sm font-semibold text-ink-900">{this.props.title ?? "이 부분을 그리지 못했습니다"}</h3>
        <p className="mt-1 text-sm text-ink-700">
          화면이 기대한 값을 서버가 주지 않았습니다. 다른 부분은 그대로 쓸 수 있습니다.
        </p>
        <p className="mt-2 text-[11px] text-ink-500">{this.state.message}</p>
        <button
          type="button"
          className="mt-3 rounded-lg bg-ink-900 px-3 py-1.5 text-xs font-medium text-white"
          onClick={() => this.setState({ message: null })}
        >
          다시 그리기
        </button>
      </div>
    );
  }
}
