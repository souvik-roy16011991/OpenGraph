"use client";

import * as React from "react";
import { useTheme } from "next-themes";

interface Node {
    x: number;
    y: number;
    vx: number;
    vy: number;
    radius: number;
}

interface Edge {
    from: number;
    to: number;
    opacity: number;
}

export function AuthAnimation({ density = 0.5 }: { density?: number }) {
    const canvasRef = React.useRef<HTMLCanvasElement>(null);
    const { theme } = useTheme();
    const [mounted, setMounted] = React.useState(false);

    React.useEffect(() => {
        setMounted(true);
    }, []);

    React.useEffect(() => {
        if (!mounted || !canvasRef.current) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        let animationFrameId: number | null = null;
        let width = 0;
        let height = 0;

        const nodes: Node[] = [];
        const maxNodes = 60;
        const connectionDist = 150;

        const seedNodes = () => {
            // Preserve existing nodes' relative positions if the canvas is
            // just resizing, but resize to the new bounds. On first call
            // (when nodes is empty) this creates the initial layout.
            if (nodes.length === 0) {
                const count = Math.max(1, Math.floor(maxNodes * density));
                for (let i = 0; i < count; i++) {
                    nodes.push({
                        x: Math.random() * Math.max(width, 1),
                        y: Math.random() * Math.max(height, 1),
                        vx: (Math.random() - 0.5) * 0.5,
                        vy: (Math.random() - 0.5) * 0.5,
                        radius: Math.random() * 2 + 1,
                    });
                }
            } else {
                // Clamp any out-of-bounds nodes after a resize so they don't
                // get stuck bouncing off an old edge.
                for (const n of nodes) {
                    if (n.x > width) n.x = width - 1;
                    if (n.y > height) n.y = height - 1;
                }
            }
        };

        const resize = () => {
            // ``offsetWidth``/``offsetHeight`` can be 0 immediately after
            // mount if the section's flex layout hasn't settled (e.g., when
            // the SSR placeholder held the slot with a fixed w-full h-full
            // size and the canvas then takes over as an absolute-positioned
            // child). Bail and let the ResizeObserver re-trigger when the
            // layout actually has non-zero bounds — without this, the canvas
            // ran its first draw loop at 0×0 and NEVER updated because the
            // window-level ``resize`` event doesn't fire on parent layout
            // changes; the animation would look "missing" to the user.
            const w = canvas.offsetWidth;
            const h = canvas.offsetHeight;
            if (w === 0 || h === 0) return;
            width = canvas.width = w;
            height = canvas.height = h;
            seedNodes();
        };

        // ResizeObserver covers layout changes that window.resize misses —
        // specifically, the first paint after the pre-mount div swaps for
        // the absolute canvas, and any later parent-size changes from the
        // form inputs growing/shrinking.
        const ro = new ResizeObserver(() => resize());
        ro.observe(canvas);
        window.addEventListener("resize", resize);
        // Also run once after the next paint so we capture the initial size
        // even if the ResizeObserver's first callback hasn't fired yet.
        requestAnimationFrame(resize);

        const draw = () => {
            if (!ctx) return;
            // Skip the frame if the canvas hasn't been measured yet — avoids
            // painting into a 0×0 bitmap that stays blank forever.
            if (width === 0 || height === 0) {
                animationFrameId = requestAnimationFrame(draw);
                return;
            }
            ctx.clearRect(0, 0, width, height);

            const isDark = theme === "dark";
            const color = isDark ? 255 : 0;
            const nodeColor = `rgba(${color}, ${color}, ${color}, 0.5)`;

            // Update and draw nodes
            nodes.forEach((node, i) => {
                node.x += node.vx;
                node.y += node.vy;

                if (node.x < 0 || node.x > width) node.vx *= -1;
                if (node.y < 0 || node.y > height) node.vy *= -1;

                ctx.beginPath();
                ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
                ctx.fillStyle = nodeColor;
                ctx.fill();

                // Check connections
                for (let j = i + 1; j < nodes.length; j++) {
                    const other = nodes[j];
                    const dx = node.x - other.x;
                    const dy = node.y - other.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist < connectionDist) {
                        ctx.beginPath();
                        ctx.moveTo(node.x, node.y);
                        ctx.lineTo(other.x, other.y);
                        ctx.strokeStyle = `rgba(${color}, ${color}, ${color}, ${0.2 * (1 - dist / connectionDist)})`;
                        ctx.lineWidth = 0.5;
                        ctx.stroke();
                    }
                }
            });

            animationFrameId = requestAnimationFrame(draw);
        };

        draw();

        return () => {
            ro.disconnect();
            window.removeEventListener("resize", resize);
            if (animationFrameId !== null) cancelAnimationFrame(animationFrameId);
        };
    }, [mounted, theme, density]);

    // Pre-mount placeholder uses the SAME absolute positioning as the real
    // canvas so swapping it in doesn't disrupt the parent section's flex
    // layout (the original ``w-full h-full`` placeholder was a flow child
    // that grew to fill the section and then collapsed to 0 when the
    // absolute canvas replaced it — the canvas ended up sized against an
    // already-0-height parent on some devices).
    if (!mounted) return <div className="absolute inset-0 bg-muted/20" />;

    return (
        <canvas
            ref={canvasRef}
            className="absolute inset-0 w-full h-full"
            style={{ opacity: 0.8 }}
        />
    );
}
