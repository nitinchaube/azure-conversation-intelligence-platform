// Browser mic capture, 48kHz -> 16kHz downsample, Int16 LE PCM over WebSocket.
// Subscribes to SSE for transcripts and compliance events.

const sid = Math.random().toString(36).slice(2, 10);
document.getElementById("sid").textContent = sid;

const wsScheme = location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${wsScheme}://${location.host}/api/voice/stream/${sid}`;
const sseUrl = `/api/voice/events/${sid}`;

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const dot = document.getElementById("dot");
const statusText = document.getElementById("statusText");
const transcriptEl = document.getElementById("transcript");
const overallScore = document.getElementById("overallScore");
const scoreBanner = document.getElementById("scoreBanner");
const ruleList = document.getElementById("ruleList");

let audioCtx = null;
let mediaStream = null;
let source = null;
let processor = null;
let ws = null;
let sse = null;
let partialEl = null;

function setStatus(text, live) {
  statusText.textContent = text;
  dot.classList.toggle("live", !!live);
}

function appendFinal(text) {
  if (partialEl) { partialEl.remove(); partialEl = null; }
  const div = document.createElement("div");
  div.className = "final";
  div.textContent = text;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function updatePartial(text) {
  if (!partialEl) {
    partialEl = document.createElement("div");
    partialEl.className = "partial";
    transcriptEl.appendChild(partialEl);
  }
  partialEl.textContent = text;
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function renderCompliance(data) {
  overallScore.textContent = Math.round(data.overall);
  scoreBanner.classList.remove("poor", "mid");
  if (data.overall < 60) scoreBanner.classList.add("poor");
  else if (data.overall < 85) scoreBanner.classList.add("mid");

  ruleList.innerHTML = "";
  for (const r of data.rules) {
    const row = document.createElement("div");
    row.className = "rule";
    const icon = document.createElement("span");
    icon.className = "rule-icon " + (r.not_applicable ? "na" : (r.passed ? "pass" : "fail"));
    icon.textContent = r.not_applicable ? "·" : (r.passed ? "✓" : "✗");
    const body = document.createElement("div");
    body.className = "rule-body";
    const name = document.createElement("div");
    name.className = "rule-name";
    name.innerHTML = `${r.name}<span class="sev ${r.severity}">${r.severity}</span>`;
    const rat = document.createElement("div");
    rat.className = "rationale";
    rat.textContent = r.rationale || (r.not_applicable ? "Not enough conversation yet to evaluate." : "");
    body.appendChild(name);
    body.appendChild(rat);
    row.appendChild(icon);
    row.appendChild(body);
    ruleList.appendChild(row);
  }
}

// ----- Audio downsample 48k -> 16k, Float32 -> Int16 LE PCM -----
function floatTo16kInt16(buffer, srcRate) {
  const ratio = srcRate / 16000;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Int16Array(newLength);
  let i = 0, off = 0;
  while (i < newLength) {
    const next = Math.round((i + 1) * ratio);
    let sum = 0, count = 0;
    for (let j = off; j < next && j < buffer.length; j++) {
      sum += buffer[j];
      count++;
    }
    const avg = count ? sum / count : 0;
    result[i] = Math.max(-32768, Math.min(32767, Math.round(avg * 32768)));
    i++;
    off = next;
  }
  return result;
}

async function start() {
  startBtn.disabled = true;
  setStatus("connecting…", false);

  // SSE first
  sse = new EventSource(sseUrl);
  sse.addEventListener("transcript_partial", (e) => updatePartial(JSON.parse(e.data).text));
  sse.addEventListener("transcript_final",   (e) => appendFinal(JSON.parse(e.data).text));
  sse.addEventListener("compliance_update",  (e) => renderCompliance(JSON.parse(e.data)));
  sse.addEventListener("error",              (e) => console.error("sse error", e));

  // Mic
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    setStatus("mic denied", false);
    startBtn.disabled = false;
    return;
  }

  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const srcRate = audioCtx.sampleRate;
  source = audioCtx.createMediaStreamSource(mediaStream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);

  // WebSocket
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    setStatus("recording", true);
    stopBtn.disabled = false;
    processor.onaudioprocess = (evt) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const channel = evt.inputBuffer.getChannelData(0);
      const pcm16 = floatTo16kInt16(channel, srcRate);
      ws.send(pcm16.buffer);
    };
    source.connect(processor);
    processor.connect(audioCtx.destination);
  };
  ws.onclose = () => setStatus("idle", false);
  ws.onerror = () => setStatus("ws error", false);
}

async function stop() {
  stopBtn.disabled = true;
  try {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send("__end__");
      ws.close();
    }
    if (processor) { processor.disconnect(); processor.onaudioprocess = null; }
    if (source) source.disconnect();
    if (audioCtx) await audioCtx.close();
    if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    if (sse) sse.close();
  } finally {
    audioCtx = mediaStream = source = processor = ws = sse = null;
    setStatus("idle", false);
    startBtn.disabled = false;
  }
}

startBtn.addEventListener("click", start);
stopBtn.addEventListener("click", stop);
