"use client";

import * as React from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function FormField({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-center gap-1.5">
        <Label className="text-xs font-medium">{label}</Label>
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="h-3 w-3 text-muted-foreground cursor-help" />
          </TooltipTrigger>
          <TooltipContent>{hint}</TooltipContent>
        </Tooltip>
      </div>
      {children}
    </div>
  );
}

export function NumberSlider({
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(v[0])}
        className="flex-1"
      />
      <Input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (!Number.isNaN(n)) onChange(n);
        }}
        className="w-24 font-mono text-xs"
      />
    </div>
  );
}

export function ChipsEditor({
  values,
  onChange,
  placeholder = "Add item and press Enter",
  options,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  options?: string[];
}) {
  const [input, setInput] = React.useState("");
  function add(val: string) {
    const v = val.trim();
    if (!v || values.includes(v)) return;
    onChange([...values, v]);
    setInput("");
  }
  function remove(v: string) {
    onChange(values.filter((x) => x !== v));
  }
  return (
    <div className="rounded-md border bg-background p-2 flex flex-wrap items-center gap-1.5 min-h-[38px]">
      {values.map((v) => (
        <Badge key={v} variant="secondary" className="gap-1 cursor-pointer" onClick={() => remove(v)}>
          {v} <span aria-hidden className="text-muted-foreground hover:text-destructive">×</span>
        </Badge>
      ))}
      {options ? (
        <select
          className="flex-1 bg-transparent outline-none text-sm"
          onChange={(e) => {
            if (e.target.value) {
              add(e.target.value);
              e.target.value = "";
            }
          }}
        >
          <option value="">{placeholder}</option>
          {options.filter((o) => !values.includes(o)).map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      ) : (
        <input
          className="flex-1 bg-transparent outline-none text-sm min-w-[120px]"
          placeholder={placeholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(input);
            }
          }}
          onBlur={() => add(input)}
        />
      )}
    </div>
  );
}

export function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center gap-2">
      <Switch checked={value} onCheckedChange={onChange} />
      <span className="text-xs text-muted-foreground">{value ? "Enabled" : "Disabled"}</span>
    </div>
  );
}
