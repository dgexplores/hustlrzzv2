"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import { downloadJson } from "@/lib/download";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InsightSection, ScoreDial } from "@/components/ui/insight";
import { Input, Label } from "@/components/ui/input";
import { useAudio } from "@/hooks/useAudio";
import { CameraPanel } from "@/components/interview/CameraPanel";
import { useMetrics } from "@/context/MetricsContext";
import {
  AlertCircle, ArrowRight, Bot, CheckCircle2, Download, FileText,
  Loader2, MessageSquareText, Mic, MicOff, RefreshCw, Send, Sparkles,
  Square, Target, UserRound, Volume2,
} from "lucide-react";

interface Turn { role: "candidate" | "interviewer"; text: string }
interface WorkflowOption {
  workflow_id: string;
  title?: string;
  company?: string;
  questions?: unknown[];
  match?: { overall_match_percent?: number };
  created_at?: string;
}
type SessionPhase = "setup" | "connecting" | "live" | "ending" | "complete";

export function InterviewPanel() {
  const [workflows, setWorkflows] = useState<WorkflowOption[]>([]);
  const [workflowId, setWorkflowId] = useState("");
  const [duration, setDuration] = useState(15);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<SessionPhase>("setup");
  const [loadingWorkflows, setLoadingWorkflows] = useState(true);
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [report, setReport] = useState<any>(null);
  const [audioMode, setAudioMode] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const metrics = useMetrics((state) => state.metrics);
  const resetMetrics = useMetrics((state) => state.reset);

  const connected = phase === "live" || phase === "ending";
  const selectedWorkflow = useMemo(
    () => workflows.find((workflow) => workflow.workflow_id === workflowId),
    [workflowId, workflows],
  );

  const { supported: audioSupported, listening, start: startMic, stop: stopMic, speak } =
    useAudio((text) => send(text));

  useEffect(() => {
    api<{ data: WorkflowOption[] }>("/workflows")
      .then((response) => {
        const ordered = [...(response.data || [])].reverse();
        setWorkflows(ordered);
        if (ordered[0]) setWorkflowId(ordered[0].workflow_id);
      })
      .catch((error) => setSessionError(error instanceof Error ? error.message : "Prepared packs could not be loaded."))
      .finally(() => setLoadingWorkflows(false));
    return () => wsRef.current?.close();
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [turns, awaitingReply]);

  useEffect(() => {
    if (phase !== "live") return;
    const timer = window.setInterval(() => setElapsedSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const begin = async () => {
    if (!workflowId) {
      setSessionError("Create or select a prepared interview pack first.");
      return;
    }
    setPhase("connecting");
    setSessionError(null);
    setReport(null);
    setTurns([]);
    setElapsedSeconds(0);
    resetMetrics();
    try {
      const response = await api<{ data: { session_id: string; websocket_parameter: string } }>("/interviews/start", {
        method: "POST",
        body: JSON.stringify({ workflow_id: workflowId, duration, is_audio: audioMode && audioSupported }),
      });
      connectWs(response.data.session_id, response.data.websocket_parameter);
    } catch (error) {
      setPhase("setup");
      setSessionError(error instanceof Error ? error.message : "Unable to start the interview.");
    }
  };

  const connectWs = (sessionId: string, query: string) => {
    wsRef.current?.close();
    const socket = new WebSocket(wsUrl(`/ws/${sessionId}${query}`, {}));
    socket.onopen = () => setPhase("live");
    socket.onmessage = (event) => {
      let message: any;
      try {
        message = JSON.parse(event.data);
      } catch {
        setAwaitingReply(false);
        setSessionError("The interviewer sent an unreadable response. Restart this session.");
        return;
      }
      if (message.type === "question" || message.type === "message") {
        const data = message.data || {};
        const text = data.message || data.question || "";
        setAwaitingReply(false);
        if (text) {
          setTurns((current) => [...current, { role: "interviewer", text }]);
          if (audioMode && audioSupported) speak(text);
        }
      } else if (message.type === "report") {
        setAwaitingReply(false);
        setReport(message.data);
        setPhase("complete");
      } else if (message.type === "error") {
        setAwaitingReply(false);
        setSessionError(message.data?.message || "The interviewer could not process that answer. Try again.");
      }
    };
    socket.onerror = () => {
      setAwaitingReply(false);
      setSessionError("The live connection was interrupted. Your preparation pack is still safe.");
    };
    socket.onclose = () => setPhase((current) => current === "complete" ? current : "setup");
    wsRef.current = socket;
  };

  const send = (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || awaitingReply || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "message", text }));
    setSessionError(null);
    setAwaitingReply(true);
    setTurns((current) => [...current, { role: "candidate", text }]);
    setInput("");
  };

  const end = () => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    stopMic();
    setPhase("ending");
    setAwaitingReply(true);
    wsRef.current.send(JSON.stringify({ type: "end" }));
  };

  const restart = () => {
    wsRef.current?.close();
    setPhase("setup");
    setTurns([]);
    setReport(null);
    setSessionError(null);
    setAwaitingReply(false);
  };

  const elapsed = `${String(Math.floor(elapsedSeconds / 60)).padStart(2, "0")}:${String(elapsedSeconds % 60).padStart(2, "0")}`;

  return (
    <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-8 md:px-6">
      <section className="motion-enter flex flex-col gap-4 pb-2 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <h1 className="text-4xl font-semibold leading-[1.08] tracking-[-0.04em] md:text-5xl">Run a realistic interview.</h1>
          <p className="mt-4 max-w-2xl leading-7 text-muted-foreground">Choose a prepared pack, answer by voice or text, and review your content and delivery together.</p>
        </div>
        <div className="flex items-center gap-2 rounded-full border bg-card px-3 py-2 text-sm shadow-sm">
          <span className={`h-2 w-2 rounded-full ${phase === "live" ? "bg-emerald-500 animate-pulse" : phase === "connecting" || phase === "ending" ? "bg-amber-500" : "bg-muted-foreground/40"}`} />
          <span className="font-medium capitalize">{phase === "setup" ? "Ready to configure" : phase}</span>
          {phase === "live" && <span className="font-mono text-muted-foreground">{elapsed}</span>}
        </div>
      </section>

      {sessionError && <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{sessionError}</span></div>}

      {phase === "setup" || phase === "connecting" ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
          <Card className="overflow-hidden">
            <CardHeader className="border-b bg-secondary/25">
              <p className="text-sm font-medium text-primary">Interview brief</p>
              <CardTitle className="text-xl">Choose your interview brief</CardTitle>
              <p className="text-sm text-muted-foreground">The interviewer uses its questions, company context, and your candidate-owned knowledge.</p>
            </CardHeader>
            <CardContent className="space-y-5 pt-6">
              {loadingWorkflows ? <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading prepared packs…</div> : workflows.length ? (
                <div className="space-y-2">
                  <Label htmlFor="workflow">Prepared pack</Label>
                  <select id="workflow" value={workflowId} onChange={(event) => setWorkflowId(event.target.value)} className="h-11 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring">
                    {workflows.map((workflow) => <option key={workflow.workflow_id} value={workflow.workflow_id}>{workflow.company ? `${workflow.company}: ` : ""}{workflow.title || "Prepared interview"}</option>)}
                  </select>
                </div>
              ) : <div className="rounded-xl border border-dashed p-6 text-center"><FileText className="mx-auto h-6 w-6 text-muted-foreground" /><p className="mt-2 font-medium">No prepared pack yet</p><p className="mt-1 text-sm text-muted-foreground">Use Prepare first to create a grounded interview.</p><Link href="/prepare" className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-primary">Open Prepare <ArrowRight className="h-4 w-4" /></Link></div>}

              {selectedWorkflow && <div className="grid gap-3 rounded-xl border bg-secondary/20 p-4 sm:grid-cols-3"><BriefStat label="Company" value={selectedWorkflow.company || "Target role"} /><BriefStat label="Questions" value={String(selectedWorkflow.questions?.length ?? 0)} /><BriefStat label="Role match" value={selectedWorkflow.match?.overall_match_percent != null ? `${selectedWorkflow.match.overall_match_percent}%` : "Prepared"} /></div>}

              <div className="space-y-2"><Label>Session length</Label><div className="grid grid-cols-4 gap-2">{[10, 15, 30, 45].map((minutes) => <button key={minutes} type="button" onClick={() => setDuration(minutes)} className={`min-h-11 rounded-lg border px-2 text-sm font-semibold surface-transition ${duration === minutes ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-accent"}`}>{minutes} min</button>)}</div></div>

              <label className={`flex min-h-16 cursor-pointer items-center justify-between gap-4 rounded-xl border p-4 surface-transition ${audioMode ? "border-primary/40 bg-primary/5" : "bg-background"}`}>
                <span className="flex items-center gap-3"><span className="rounded-lg bg-primary/10 p-2 text-primary"><Volume2 className="h-5 w-5" /></span><span><span className="block text-sm font-semibold">Voice interview</span><span className="block text-xs text-muted-foreground">Hear questions and answer through your microphone.</span></span></span>
                <input type="checkbox" checked={audioMode && audioSupported} disabled={!audioSupported} onChange={(event) => setAudioMode(event.target.checked)} className="h-5 w-5 accent-primary" aria-label="Enable voice interview" />
              </label>
              {!audioSupported && <p className="text-xs text-muted-foreground">Voice input is unavailable in this browser; typed interviews still work fully.</p>}

              <Button size="lg" onClick={begin} disabled={phase === "connecting" || !workflowId} className="w-full">
                {phase === "connecting" ? <><Loader2 className="h-4 w-4 animate-spin" />Connecting securely…</> : <>Enter interview studio <ArrowRight className="h-4 w-4" /></>}
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><p className="text-sm font-medium text-primary">Before you begin</p><CardTitle className="text-xl">Session readiness</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <Readiness icon={<Target className="h-4 w-4" />} title="Grounded questions" copy="Questions come from your selected role, resume, and current company brief." />
              <Readiness icon={<Mic className="h-4 w-4" />} title="Natural responses" copy="Speak or type in your own words; the coach can probe shallow answers." />
              <Readiness icon={<Sparkles className="h-4 w-4" />} title="Private presence feedback" copy="Posture, gaze, and gesture signals run locally in your browser." />
              <div className="rounded-xl bg-secondary/50 p-4 text-xs leading-5 text-muted-foreground">Tip: answer behavioral questions with Situation → Task → Action → Result, then add what you learned.</div>
            </CardContent>
          </Card>
        </div>
      ) : phase === "complete" && report ? (
        <ReportPanel report={report} metrics={metrics} onRestart={restart} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(360px,0.82fr)_minmax(0,1.18fr)]">
          <div className="space-y-6"><Card><CardHeader className="flex-row items-center justify-between space-y-0"><div><CardTitle className="text-lg">Presence coach</CardTitle><p className="mt-1 text-xs text-muted-foreground">Processed privately on this device</p></div><span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400">Local only</span></CardHeader><CardContent><CameraPanel /></CardContent></Card></div>

          <Card className="flex min-h-[680px] flex-col overflow-hidden">
            <CardHeader className="flex-row items-center justify-between space-y-0 border-b bg-secondary/20">
              <div><CardTitle className="flex items-center gap-2 text-lg"><Bot className="h-5 w-5 text-primary" />AI interviewer</CardTitle><p className="mt-1 text-xs text-muted-foreground">{selectedWorkflow?.company || "Role-specific"} · {turns.filter((turn) => turn.role === "candidate").length} answers</p></div>
              <Button variant="outline" size="sm" onClick={end} disabled={phase === "ending"}>{phase === "ending" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-3.5 w-3.5" />} End &amp; score</Button>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col p-0">
              <div aria-live="polite" className="flex-1 space-y-5 overflow-y-auto p-4 md:p-6">
                {turns.map((turn, index) => <TranscriptTurn key={`${turn.role}-${index}`} turn={turn} />)}
                {awaitingReply && <div className="flex items-center gap-3 text-sm text-muted-foreground"><span className="rounded-full bg-primary/10 p-2 text-primary"><Bot className="h-4 w-4" /></span><span className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" />{phase === "ending" ? "Building your coaching report…" : "Interviewer is considering your answer…"}</span></div>}
                <div ref={transcriptEndRef} />
              </div>
              <div className="border-t bg-background p-3 md:p-4">
                <div className="flex gap-2">
                  {audioMode && audioSupported && <Button type="button" onClick={listening ? stopMic : startMic} disabled={!connected || awaitingReply} variant={listening ? "destructive" : "secondary"} size="icon" aria-label={listening ? "Stop microphone" : "Start microphone"}>{listening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}</Button>}
                  <Input value={input} maxLength={12000} disabled={!connected || awaitingReply} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder={awaitingReply ? "Waiting for interviewer…" : "Answer with a concrete example…"} aria-label="Interview answer" />
                  <Button type="button" size="icon" onClick={() => send()} disabled={!connected || awaitingReply || !input.trim()} aria-label="Send answer"><Send className="h-4 w-4" /></Button>
                </div>
                <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground"><span>Enter to send</span><span>{input.length}/12,000</span></div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </main>
  );
}

function BriefStat({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 truncate text-sm font-semibold">{value}</p></div>;
}

function Readiness({ icon, title, copy }: { icon: React.ReactNode; title: string; copy: string }) {
  return <div className="flex gap-3"><span className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary">{icon}</span><div><p className="text-sm font-semibold">{title}</p><p className="mt-0.5 text-sm leading-6 text-muted-foreground">{copy}</p></div></div>;
}

function TranscriptTurn({ turn }: { turn: Turn }) {
  const candidate = turn.role === "candidate";
  return <div className={`flex gap-3 ${candidate ? "flex-row-reverse" : ""}`}><span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${candidate ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground"}`}>{candidate ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4" />}</span><div className={`max-w-[85%] ${candidate ? "text-right" : ""}`}><p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{candidate ? "You" : "Interviewer"}</p><div className={`inline-block rounded-2xl px-4 py-3 text-left text-sm leading-6 ${candidate ? "rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm border bg-card"}`}>{turn.text}</div></div></div>;
}

function ReportPanel({ report, metrics, onRestart }: { report: any; metrics: ReturnType<typeof useMetrics.getState>["metrics"]; onRestart: () => void }) {
  const scores = Object.entries(report?.scores || {}) as [string, number][];
  const overallScore = scores.length ? scores.reduce((sum, [, value]) => sum + (Number(value) || 0), 0) / scores.length : 0;
  const presence = [
    ["Gestures", metrics.handDetectionCounter, `${metrics.handDetectionDuration.toFixed(0)}s active`],
    ["Gaze resets", metrics.notFacingCounter, `${metrics.notFacingDuration.toFixed(0)}s away`],
    ["Posture resets", metrics.badPostureDetectionCounter, `${metrics.badPostureDuration.toFixed(0)}s adjusting`],
  ] as const;
  const exportData = { ...report, local_presence_metrics: metrics };
  return <div className="space-y-6">
    <Card className="insight-hero overflow-hidden"><CardContent className="grid gap-6 p-6 lg:grid-cols-[1.2fr_auto]"><div><span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400"><CheckCircle2 className="h-3.5 w-3.5" />Session complete</span><h2 className="mt-4 text-3xl font-semibold tracking-tight">Your coaching debrief</h2><p className="mt-3 max-w-2xl leading-7 text-muted-foreground">{report?.summary || "Your report has been saved to practice history."}</p>{report?.verdict && <p className="mt-4 text-sm leading-6"><span className="font-semibold">Coach verdict:</span> {report.verdict}</p>}</div><div className="flex flex-col items-end justify-between gap-4"><ScoreDial value={overallScore} label="overall" /><div className="flex w-full flex-col gap-2"><Button onClick={() => downloadJson("hustlrzz-coaching-report.json", exportData)}><Download className="h-4 w-4" />Export full report</Button><Button variant="outline" onClick={onRestart}><RefreshCw className="h-4 w-4" />Practice another session</Button></div></div></CardContent></Card>
    <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Target className="h-5 w-5 text-primary" />Answer quality</CardTitle></CardHeader><CardContent className="space-y-4">{scores.length ? scores.map(([label, rawScore]) => { const score = Number(rawScore) || 0; return <div key={label}><div className="mb-1.5 flex justify-between text-sm"><span className="font-medium capitalize">{label.replace(/_/g, " ")}</span><span className="font-semibold">{score}/100</span></div><div className="h-2 overflow-hidden rounded-full bg-secondary"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(0, Math.min(score, 100))}%` }} /></div></div>; }) : <p className="text-sm text-muted-foreground">No numerical scores were returned.</p>}</CardContent></Card>
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><MessageSquareText className="h-5 w-5 text-primary" />Local presence signals</CardTitle><p className="text-xs text-muted-foreground">These measurements stayed in your browser.</p></CardHeader><CardContent className="grid grid-cols-3 gap-3">{presence.map(([label, value, detail]) => <div key={label} className="rounded-xl bg-secondary/50 p-3"><p className="text-2xl font-semibold">{value}</p><p className="mt-1 text-xs font-medium">{label}</p><p className="mt-1 text-[11px] text-muted-foreground">{detail}</p></div>)}</CardContent></Card>
    </div>
    <div className="grid gap-6 lg:grid-cols-2"><FeedbackList title="Keep doing this" items={report?.strengths || []} positive /><FeedbackList title="Your next practice focus" items={report?.improvements || []} /></div>
  </div>;
}

function FeedbackList({ title, items, positive = false }: { title: string; items: string[]; positive?: boolean }) {
  return <InsightSection eyebrow={positive ? "Keep" : "Improve"} title={title} description={positive ? "Bring these habits into your next answer." : "Choose one of these for your next session."}>{items.length ? <ul className="space-y-3">{items.slice(0, 5).map((item, index) => <li key={`${item}-${index}`} className="flex gap-3 text-sm leading-6"><span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${positive ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-primary/10 text-primary"}`}>{index + 1}</span><span>{item}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">No additional notes were returned.</p>}</InsightSection>;
}
