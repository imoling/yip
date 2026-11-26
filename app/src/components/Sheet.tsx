// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
// 主窗口内的浮层：标题固定，正文滚动，Esc 或点空白处关闭。
import { useEffect, type ReactNode } from "react";
import { Icon } from "./Icon";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  foot?: ReactNode;
  head?: ReactNode;
  wide?: boolean;
  tall?: boolean;
  flush?: boolean;
}

export function Sheet({ title, onClose, children, foot, head, wide, tall, flush }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`sheet ${wide ? "wide" : ""} ${tall ? "tall" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-head">
          <h2>{title}</h2>
          <span className="sp" />
          {head}
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className={`sheet-body ${flush ? "flush" : ""}`}>{children}</div>
        {foot && <div className="sheet-foot">{foot}</div>}
      </div>
    </div>
  );
}
