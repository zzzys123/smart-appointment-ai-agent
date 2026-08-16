import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result } from "antd";

interface Props { children: ReactNode }
interface State { hasError: boolean }

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("React page crashed", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="500"
          title="页面暂时无法显示"
          subTitle="前端捕获到异常。你可以刷新页面恢复，不会影响后端数据。"
          extra={<Button type="primary" onClick={() => window.location.reload()}>刷新页面</Button>}
        />
      );
    }
    return this.props.children;
  }
}
