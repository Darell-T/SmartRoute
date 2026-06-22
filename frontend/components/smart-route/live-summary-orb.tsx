"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

export type SummaryOrbPhase = "idle" | "thinking" | "scattering";

interface Props {
  phase: SummaryOrbPhase;
  onScatterComplete?: () => void;
  compact?: boolean;
  speaking?: boolean;
  color?: number;
  contained?: boolean;
}

type OrbPhase = "idle" | "thinking" | "scattering";

interface Orb {
  setPhase(p: OrbPhase, onComplete?: () => void): void;
  setSpeaking(s: boolean): void;
  setColor(color: number): void;
  destroy(): void;
}

function createOrb(
  canvas: HTMLCanvasElement,
  container: HTMLElement,
  opts: { compact?: boolean; color?: number; contained?: boolean } = {},
): Orb {
  const { compact = false } = opts;
  const contained = Boolean(opts.contained);
  let destroyed = false;
  let speaking = false;
  let thinkingColor = new THREE.Color(opts.color ?? 0x6ec4ff);
  const idleColor = new THREE.Color(0x4ca8e8);

  function containerSize() {
    const rect = container.getBoundingClientRect();
    return { w: Math.round(rect.width) || 1, h: Math.round(rect.height) || 1 };
  }

  const { w: initW, h: initH } = containerSize();
  const isMobile = initW < 768;
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const N = prefersReducedMotion
    ? compact
      ? 220
      : 600
    : compact
      ? 720
      : isMobile
        ? 1400
        : 2200;
  const MAX_LINES = prefersReducedMotion
    ? 0
    : compact
      ? 900
      : isMobile
        ? 5000
        : 9000;
  const MAX_ELECTRONS = prefersReducedMotion ? 0 : compact ? 36 : 220;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: compact,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(initW, initH);
  renderer.setClearColor(0x000000, compact ? 0 : 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, initW / initH, 1, 1000);
  const baseZ = compact ? 52 : isMobile ? 88 : 78;
  const scaleFactor =
    Math.min(initW, initH) / (compact ? 80 : isMobile ? 420 : 620);
  camera.position.z = contained
    ? 40
    : baseZ / Math.max(scaleFactor, 0.5);

  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const pPhase = new Float32Array(N);
  const shellDir = contained ? new Float32Array(N * 3) : null;

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const nx = Math.sin(phi) * Math.cos(theta);
    const ny = Math.sin(phi) * Math.sin(theta);
    const nz = Math.cos(phi);
    const radiusBias = contained
      ? 0.78 + Math.random() * 0.22
      : Math.pow(Math.random(), 0.5);
    const r = radiusBias * (contained ? 18.5 : 25);
    const i3 = i * 3;
    pos[i3] = r * nx;
    pos[i3 + 1] = r * ny;
    pos[i3 + 2] = r * nz;
    if (shellDir) {
      shellDir[i3] = nx;
      shellDir[i3 + 1] = ny;
      shellDir[i3 + 2] = nz;
    }
    pPhase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

  const mat = new THREE.PointsMaterial({
    color: 0x4ca8e8,
    size: 0.45,
    transparent: true,
    opacity: 0.6,
    sizeAttenuation: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new THREE.LineBasicMaterial({
    color: 0x6ec4ff,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const lines = new THREE.LineSegments(lineGeo, lineMat);
  scene.add(lines);

  const electronGeo = new THREE.BufferGeometry();
  const electronPos = new Float32Array(MAX_ELECTRONS * 3);
  electronGeo.setAttribute(
    "position",
    new THREE.BufferAttribute(electronPos, 3),
  );
  electronGeo.setDrawRange(0, 0);

  const electronMat = new THREE.PointsMaterial({
    color: 0xffffff,
    size: 0.8,
    transparent: true,
    opacity: 1,
    sizeAttenuation: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const electrons = new THREE.Points(electronGeo, electronMat);
  scene.add(electrons);

  interface Electron {
    sx: number;
    sy: number;
    sz: number;
    ex: number;
    ey: number;
    ez: number;
    t: number;
    speed: number;
  }

  const activeElectrons: Electron[] = [];
  let electronSpawnRate = 0;
  let targetElectronRate = 0;
  let lastElectronSpawn = 0;

  let activeConnections: Array<{
    x1: number;
    y1: number;
    z1: number;
    x2: number;
    y2: number;
    z2: number;
  }> = [];

  let phase: OrbPhase = "idle";
  let targetRadius = 18;
  let currentRadius = 18;
  let targetSpeed = 0.2;
  let currentSpeed = 0.2;
  let targetBright = 0.5;
  let currentBright = 0.5;
  let targetSize = 0.42;
  let currentSize = 0.42;
  let lineAmount = 0;
  let targetLineAmount = 0.35;
  const lineDistance = 8;

  let spinX = 0;
  let spinY = 0;
  let spinZ = 0;
  let transitionEnergy = 0;
  let lastPhase: OrbPhase = "idle";

  let cloudZ = 0;
  let cloudZVel = 0;
  let shellBreath = 1;

  const scatVel = new Float32Array(N * 3);
  let scatterT0 = 0;
  let scatterFired = false;
  let scatterCallback: (() => void) | null = null;
  let scatterOpacity = 1;

  const clock = new THREE.Clock();
  let lastTime = 0;
  const lineStep = Math.max(1, Math.floor(N / (isMobile ? 360 : 540)));

  function animate() {
    if (destroyed) return;
    requestAnimationFrame(animate);

    const t = clock.getElapsedTime();
    const dt = Math.min(t - lastTime, 0.05);
    lastTime = t;

    if (phase === "idle") {
      targetRadius = 18;
      targetSpeed = 0.2;
      targetBright = 0.52;
      targetSize = 0.42;
      targetLineAmount = prefersReducedMotion ? 0 : 0.35;
      targetElectronRate = 0;
    } else if (phase === "thinking") {
      targetRadius = contained ? 14.5 : 16;
      targetSpeed = contained ? 0.62 : 0.5;
      targetBright = contained ? 0.82 : 0.72;
      targetSize = contained ? 0.34 : 0.3;
      targetLineAmount = 1;
      targetElectronRate = 0.015;
    }

    if (speaking) {
      const pulse = 0.85 + Math.sin(t * 6.5) * 0.15;
      targetBright = Math.max(targetBright, 0.85 * pulse);
      targetSpeed = Math.max(targetSpeed, 0.45);
      targetRadius = Math.max(targetRadius - 2, contained ? 12 : 14);
      targetLineAmount = Math.max(targetLineAmount, 0.6);
      targetElectronRate = Math.max(targetElectronRate, 0.012);
    }

    if (phase !== "scattering") {
      currentRadius += (targetRadius - currentRadius) * 0.02;
      currentSpeed += (targetSpeed - currentSpeed) * 0.02;
      currentBright += (targetBright - currentBright) * 0.02;
      currentSize += (targetSize - currentSize) * 0.02;
      lineAmount += (targetLineAmount - lineAmount) * 0.02;
      electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;
    }

    if (phase !== lastPhase) {
      transitionEnergy = 1;
      lastPhase = phase;
    }
    transitionEnergy *= 0.985;
    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }

    let zTarget = Math.sin(t * 0.12) * 8;
    if (phase === "thinking") {
      zTarget = contained
        ? Math.sin(t * 0.75) * 2.2 + Math.sin(t * 1.35) * 1.1
        : compact
        ? Math.sin(t * 0.3) * 4 + Math.sin(t * 0.9) * 1.5
        : Math.sin(t * 0.3) * 8 + Math.sin(t * 0.9) * 3;
    }
    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    const breathScale = contained && phase === "thinking"
      ? 1 + Math.sin(t * 1.15) * 0.085
      : 1;
    points.scale.setScalar(breathScale);
    lines.scale.setScalar(breathScale);
    electrons.scale.setScalar(breathScale);

    points.rotation.x = spinX;
    points.rotation.y = spinY;
    points.rotation.z = spinZ;
    points.position.z = cloudZ;
    lines.rotation.x = spinX;
    lines.rotation.y = spinY;
    lines.rotation.z = spinZ;
    lines.position.z = cloudZ;
    electrons.rotation.x = spinX;
    electrons.rotation.y = spinY;
    electrons.rotation.z = spinZ;
    electrons.position.z = cloudZ;

    const p = geo.getAttribute("position") as THREE.BufferAttribute;
    const a = p.array as Float32Array;

    if (phase === "scattering") {
      const td = t - scatterT0;
      const progress = Math.min(td / 1, 1);
      scatterOpacity = Math.max(1 - progress, 0);

      for (let i = 0; i < N; i++) {
        const i3 = i * 3;
        a[i3] += scatVel[i3] * dt * 60;
        a[i3 + 1] += scatVel[i3 + 1] * dt * 60;
        a[i3 + 2] += scatVel[i3 + 2] * dt * 60;
      }
      p.needsUpdate = true;
      mat.opacity = currentBright * scatterOpacity;
      lineMat.opacity *= 0.9;
      electronMat.opacity = scatterOpacity;
      lineAmount *= 0.9;

      if (progress >= 1 && !scatterFired) {
        scatterFired = true;
        scatterCallback?.();
      }
    } else {
      for (let i = 0; i < N; i++) {
        const i3 = i * 3;
        const x = a[i3];
        const y = a[i3 + 1];
        const z = a[i3 + 2];
        const px = pPhase[i];

        vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
        vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
        vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;
        vel[i3] +=
          Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
        vel[i3 + 1] +=
          Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
        vel[i3 + 2] +=
          Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

        const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
        if (shellDir && phase === "thinking") {
          shellBreath =
            1 +
            Math.sin(t * 1.25) * 0.075 +
            Math.sin(t * 2.1 + px * 0.017) * 0.022;
          const shellRadius = currentRadius * shellBreath;
          const targetX = shellDir[i3] * shellRadius;
          const targetY = shellDir[i3 + 1] * shellRadius;
          const targetZ = shellDir[i3 + 2] * shellRadius;
          const shellPull = 0.0125 * currentSpeed;
          vel[i3] += (targetX - x) * shellPull;
          vel[i3 + 1] += (targetY - y) * shellPull;
          vel[i3 + 2] += (targetZ - z) * shellPull;
        }

        const pull = shellDir && phase === "thinking"
          ? Math.max(0, dist - currentRadius * 1.1) * 0.0016
          : Math.max(0, dist - currentRadius) * 0.002 + 0.0003;
        if (pull > 0) {
          vel[i3] -= (x / dist) * pull;
          vel[i3 + 1] -= (y / dist) * pull;
          vel[i3 + 2] -= (z / dist) * pull;
        }

        vel[i3] *= 0.992;
        vel[i3 + 1] *= 0.992;
        vel[i3 + 2] *= 0.992;
        a[i3] += vel[i3];
        a[i3 + 1] += vel[i3 + 1];
        a[i3 + 2] += vel[i3 + 2];
      }
      p.needsUpdate = true;
      mat.opacity = contained && phase === "thinking"
        ? Math.min(currentBright + 0.06, 0.9)
        : currentBright;
      mat.size = currentSize;
    }

    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDistSq = lineDistance * lineDistance;

      for (let i = 0; i < N && lineCount < MAX_LINES; i += lineStep) {
        const i3 = i * 3;
        const x1 = a[i3];
        const y1 = a[i3 + 1];
        const z1 = a[i3 + 2];

        for (
          let j = i + lineStep;
          j < N && lineCount < MAX_LINES;
          j += lineStep
        ) {
          const j3 = j * 3;
          const dx = a[j3] - x1;
          const dy = a[j3 + 1] - y1;
          const dz = a[j3 + 2] - z1;
          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1;
            la[idx + 1] = y1;
            la[idx + 2] = z1;
            la[idx + 3] = a[j3];
            la[idx + 4] = a[j3 + 1];
            la[idx + 5] = a[j3 + 2];
            lineCount += 1;
          }
        }
      }

      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * (contained ? 0.2 : 0.12);
      activeConnections = [];
      for (let c = 0; c < Math.min(lineCount, 500); c++) {
        const ci = c * 6;
        activeConnections.push({
          x1: la[ci],
          y1: la[ci + 1],
          z1: la[ci + 2],
          x2: la[ci + 3],
          y2: la[ci + 4],
          z2: la[ci + 5],
        });
      }
    } else {
      lineGeo.setDrawRange(0, 0);
      activeConnections = [];
    }

    if (activeConnections.length > 0 && electronSpawnRate > 0.005) {
      if (activeElectrons.length < 3 && t - lastElectronSpawn > 1) {
        const conn =
          activeConnections[
            Math.floor(Math.random() * activeConnections.length)
          ];
        activeElectrons.push({
          sx: conn.x1,
          sy: conn.y1,
          sz: conn.z1,
          ex: conn.x2,
          ey: conn.y2,
          ez: conn.z2,
          t: 0,
          speed: 0.003 + Math.random() * 0.003,
        });
        lastElectronSpawn = t;
      }
    }

    const ep = electronGeo.getAttribute("position") as THREE.BufferAttribute;
    const ea = ep.array as Float32Array;
    let aliveCount = 0;
    for (let e = activeElectrons.length - 1; e >= 0; e--) {
      const electron = activeElectrons[e];
      electron.t += electron.speed;
      if (electron.t >= 1) {
        activeElectrons.splice(e, 1);
        continue;
      }
      const ei = aliveCount * 3;
      ea[ei] = electron.sx + (electron.ex - electron.sx) * electron.t;
      ea[ei + 1] = electron.sy + (electron.ey - electron.sy) * electron.t;
      ea[ei + 2] = electron.sz + (electron.ez - electron.sz) * electron.t;
      aliveCount += 1;
    }
    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    if (phase === "thinking") {
      mat.color.lerp(thinkingColor, 0.015);
      lineMat.color.lerp(thinkingColor, 0.015);
    } else {
      mat.color.lerp(idleColor, 0.015);
      lineMat.color.lerp(idleColor, 0.015);
    }

    camera.position.x = Math.sin(t * 0.02) * 5;
    camera.position.y = Math.cos(t * 0.03) * 3;
    camera.lookAt(0, 0, cloudZ * 0.2);
    renderer.render(scene, camera);
  }

  function onResize() {
    const { w, h } = containerSize();
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  const resizeObserver = new ResizeObserver(onResize);
  resizeObserver.observe(container);
  animate();

  return {
    setPhase(nextPhase: OrbPhase, onComplete?: () => void) {
      if (compact && nextPhase === "scattering") {
        onComplete?.();
        return;
      }
      if (nextPhase === "scattering" && phase !== "scattering") {
        const a = (geo.getAttribute("position") as THREE.BufferAttribute)
          .array as Float32Array;
        for (let i = 0; i < N; i++) {
          const i3 = i * 3;
          const x = a[i3];
          const y = a[i3 + 1];
          const z = a[i3 + 2];
          const len = Math.sqrt(x * x + y * y + z * z) || 1;
          const speed = 0.08 + Math.random() * 0.12;
          scatVel[i3] = (x / len) * speed;
          scatVel[i3 + 1] = (y / len) * speed;
          scatVel[i3 + 2] = (z / len) * speed;
        }
        scatterT0 = clock.getElapsedTime();
        scatterFired = false;
        scatterOpacity = 1;
        scatterCallback = onComplete ?? null;
      }
      phase = nextPhase;
    },
    setSpeaking(nextSpeaking: boolean) {
      speaking = nextSpeaking;
    },
    setColor(nextColor: number) {
      thinkingColor = new THREE.Color(nextColor);
    },
    destroy() {
      destroyed = true;
      resizeObserver.disconnect();
      geo.dispose();
      mat.dispose();
      lineGeo.dispose();
      lineMat.dispose();
      electronGeo.dispose();
      electronMat.dispose();
      renderer.dispose();
    },
  };
}

export default function LiveSummaryOrb({
  phase,
  onScatterComplete,
  compact = true,
  speaking = false,
  color = 0x6ec4ff,
  contained = false,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<Orb | null>(null);
  const callbackRef = useRef(onScatterComplete);
  const [visible, setVisible] = useState(false);

  callbackRef.current = onScatterComplete;

  useEffect(() => {
    const raf = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    if (!canvasRef.current || !wrapRef.current) return;
    orbRef.current = createOrb(canvasRef.current, wrapRef.current, {
      compact,
      color,
      contained,
    });
    return () => {
      orbRef.current?.destroy();
      orbRef.current = null;
    };
  }, [compact, contained]);

  useEffect(() => {
    if (!orbRef.current) return;
    if (phase === "scattering") {
      orbRef.current.setPhase("scattering", () => callbackRef.current?.());
      return;
    }
    orbRef.current.setPhase(phase);
  }, [phase]);

  useEffect(() => {
    orbRef.current?.setSpeaking(speaking);
  }, [speaking]);

  useEffect(() => {
    orbRef.current?.setColor(color);
  }, [color]);

  const radialMask = contained
    ? "radial-gradient(circle at center, #000 60%, rgba(0, 0, 0, 0.82) 78%, transparent 96%)"
    : "radial-gradient(circle at center, #000 54%, rgba(0, 0, 0, 0.82) 72%, transparent 94%)";

  return (
    <div
      ref={wrapRef}
      style={{
        position: "absolute",
        inset: contained ? 0 : compact ? "-14%" : 0,
        overflow: "hidden",
        borderRadius: "999px",
        WebkitMaskImage: radialMask,
        maskImage: radialMask,
        contain: "paint",
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          opacity: visible ? 1 : 0,
          transform: contained ? undefined : compact ? "scale(1.08)" : undefined,
          transformOrigin: "center",
          transition: "opacity 0.6s var(--sr-ease-out, cubic-bezier(0.32, 0.72, 0, 1))",
          background: "transparent",
          WebkitMaskImage: radialMask,
          maskImage: radialMask,
        }}
      />
    </div>
  );
}
