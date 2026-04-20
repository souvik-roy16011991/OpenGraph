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

        let animationFrameId: number;
        let width = 0;
        let height = 0;

        const nodes: Node[] = [];
        const maxNodes = 60;
        const connectionDist = 150;

        const resize = () => {
            width = canvas.width = canvas.offsetWidth;
            height = canvas.height = canvas.offsetHeight;

            // Initialize nodes
            nodes.length = 0;
            const count = Math.floor(maxNodes * density);
            for (let i = 0; i < count; i++) {
                nodes.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    vx: (Math.random() - 0.5) * 0.5,
                    vy: (Math.random() - 0.5) * 0.5,
                    radius: Math.random() * 2 + 1,
                });
            }
        };

        window.addEventListener("resize", resize);
        resize();

        const draw = () => {
            if (!ctx) return;
            ctx.clearRect(0, 0, width, height);

            const isDark = theme === "dark";
            const color = isDark ? 255 : 0;
            const nodeColor = `rgba(${color}, ${color}, ${color}, 0.5)`;
            const edgeColor = `rgba(${color}, ${color}, ${color}, 0.15)`;

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
            window.removeEventListener("resize", resize);
            cancelAnimationFrame(animationFrameId);
        };
    }, [mounted, theme, density]);

    if (!mounted) return <div className="w-full h-full bg-muted/20" />;

    return (
        <canvas
            ref={canvasRef}
            className="absolute inset-0 w-full h-full"
            style={{ opacity: 0.8 }}
        />
    );
}
