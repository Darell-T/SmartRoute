"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

export type IntroPhase = "idle" | "thinking" | "scattering";

interface Props {
  phase: IntroPhase;
  onScatterComplete: () => void;
}

/* ── Orb types ── */
type OrbPhase = "idle" | "thinking" | "scattering";

interface Orb {
  setPhase(p: OrbPhase, onComplete?: () => void): void;
  destroy(): void;
}

/* ── Factory ── */
function createOrb(canvas: HTMLCanvasElement): Orb {
  let destroyed = false;

  const isMobile = window.innerWidth < 768;
  const N = isMobile ? 1200 : 2000;
  const MAX_LINES = isMobile ? 4000 : 8000;
  const MAX_ELECTRONS = 200;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x050508, 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(
    45,
    window.innerWidth / window.innerHeight,
    1,
    1000,
  );
  camera.position.z = isMobile ? 90 : 80;

  /* ── Particles ── */
  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const pPhase = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.pow(Math.random(), 0.5) * 25;
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
    pPhase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

  const mat = new THREE.PointsMaterial({
    color: 0x4ca8e8,
    size: 0.4,
    transparent: true,
    opacity: 0.6,
    sizeAttenuation: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  /* ── Connection lines ── */
  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new THREE.LineBasicMaterial({
    color: 0x4ca8e8,
    transparent: true,
    opacity: 0.0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const lines = new THREE.LineSegments(lineGeo, lineMat);
  scene.add(lines);

  /* ── Electrons ── */
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
    opacity: 1.0,
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

  let activeConnections: {
    x1: number;
    y1: number;
    z1: number;
    x2: number;
    y2: number;
    z2: number;
  }[] = [];

  /* ── State ── */
  let phase: OrbPhase = "idle";
  let targetRadius = 28,
    currentRadius = 28;
  let targetSpeed = 0.2,
    currentSpeed = 0.2;
  let targetBright = 0.5,
    currentBright = 0.5;
  let targetSize = 0.35,
    currentSize = 0.35;
  let lineAmount = 0,
    targetLineAmount = 0.15;
  const lineDistance = 8;

  /* Transition tumble */
  let spinX = 0,
    spinY = 0,
    spinZ = 0;
  let transitionEnergy = 0;
  let lastPhase: OrbPhase = "idle";

  /* Depth Z breathing */
  let cloudZ = 0,
    cloudZVel = 0;

  /* Scatter state */
  const scatVel = new Float32Array(N * 3);
  let scatterT0 = 0;
  let scatterFired = false;
  let scatterCallback: (() => void) | null = null;
  let scatterOpacity = 1;

  const clock = new THREE.Clock();
  let lastTime = 0;
  const lineStep = Math.max(1, Math.floor(N / (isMobile ? 400 : 600)));

  function animate() {
    if (destroyed) return;
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    const dt = Math.min(t - lastTime, 0.05);
    lastTime = t;

    /* ── Set targets by phase ── */
    if (phase === "idle") {
      targetRadius = 28;
      targetSpeed = 0.2;
      targetBright = 0.5;
      targetSize = 0.35;
      targetLineAmount = 0.15;
      targetElectronRate = 0;
    } else if (phase === "thinking") {
      targetRadius = 16;
      targetSpeed = 0.5;
      targetBright = 0.7;
      targetSize = 0.3;
      targetLineAmount = 1.0;
      targetElectronRate = 0.015;
    }
    /* scattering targets handled separately below */

    /* ── Lerp parameters ── */
    if (phase !== "scattering") {
      currentRadius += (targetRadius - currentRadius) * 0.02;
      currentSpeed += (targetSpeed - currentSpeed) * 0.02;
      currentBright += (targetBright - currentBright) * 0.02;
      currentSize += (targetSize - currentSize) * 0.02;
      lineAmount += (targetLineAmount - lineAmount) * 0.02;
      electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;
    }

    /* ── Transition tumble ── */
    if (phase !== lastPhase) {
      transitionEnergy = 1.0;
      lastPhase = phase;
    }
    transitionEnergy *= 0.985;
    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }

    /* ── Depth Z breathing ── */
    let zTarget = Math.sin(t * 0.12) * 8;
    if (phase === "thinking") {
      zTarget = Math.sin(t * 0.3) * 15 + Math.sin(t * 0.9) * 6;
    }
    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    /* Apply rotation + Z to groups */
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

    /* ── Update particles ── */
    const p = geo.getAttribute("position") as THREE.BufferAttribute;
    const a = p.array as Float32Array;

    if (phase === "scattering") {
      /* Scatter: fly outward and fade */
      const td = t - scatterT0;
      const progress = Math.min(td / 1.0, 1);
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

      /* Fade line amount quickly */
      lineAmount *= 0.9;

      if (progress >= 1 && !scatterFired) {
        scatterFired = true;
        scatterCallback?.();
      }
    } else {
      /* Normal physics: idle or thinking */
      for (let i = 0; i < N; i++) {
        const i3 = i * 3;
        let x = a[i3],
          y = a[i3 + 1],
          z = a[i3 + 2];
        const px = pPhase[i];

        /* Organic drift */
        vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
        vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
        vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;
        vel[i3] +=
          Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
        vel[i3 + 1] +=
          Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
        vel[i3 + 2] +=
          Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

        /* Spring toward currentRadius */
        const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
        const pull = Math.max(0, dist - currentRadius) * 0.002 + 0.0003;
        vel[i3] -= (x / dist) * pull;
        vel[i3 + 1] -= (y / dist) * pull;
        vel[i3 + 2] -= (z / dist) * pull;

        /* Damping */
        vel[i3] *= 0.992;
        vel[i3 + 1] *= 0.992;
        vel[i3 + 2] *= 0.992;
        a[i3] += vel[i3];
        a[i3 + 1] += vel[i3 + 1];
        a[i3 + 2] += vel[i3 + 2];
      }
      p.needsUpdate = true;

      mat.opacity = currentBright;
      mat.size = currentSize;
    }

    /* ── Connection lines ── */
    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDist = lineDistance;
      const maxDistSq = maxDist * maxDist;

      for (let i = 0; i < N && lineCount < MAX_LINES; i += lineStep) {
        const i3 = i * 3;
        const x1 = a[i3],
          y1 = a[i3 + 1],
          z1 = a[i3 + 2];
        for (
          let j = i + lineStep;
          j < N && lineCount < MAX_LINES;
          j += lineStep
        ) {
          const j3 = j * 3;
          const dx = a[j3] - x1,
            dy = a[j3 + 1] - y1,
            dz = a[j3 + 2] - z1;
          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1;
            la[idx + 1] = y1;
            la[idx + 2] = z1;
            la[idx + 3] = a[j3];
            la[idx + 4] = a[j3 + 1];
            la[idx + 5] = a[j3 + 2];
            lineCount++;
          }
        }
      }
      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * 0.12;

      /* Store connections for electron spawning */
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

    /* ── Electrons ── */
    if (activeConnections.length > 0 && electronSpawnRate > 0.005) {
      if (activeElectrons.length < 3 && t - lastElectronSpawn > 1.0) {
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
      const el = activeElectrons[e];
      el.t += el.speed;
      if (el.t >= 1) {
        activeElectrons.splice(e, 1);
        continue;
      }
      const ei = aliveCount * 3;
      ea[ei] = el.sx + (el.ex - el.sx) * el.t;
      ea[ei + 1] = el.sy + (el.ey - el.sy) * el.t;
      ea[ei + 2] = el.sz + (el.ez - el.sz) * el.t;
      aliveCount++;
    }
    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    /* ── Color transitions ── */
    if (phase === "thinking") {
      mat.color.lerp(new THREE.Color(0x6ec4ff), 0.015);
      lineMat.color.lerp(new THREE.Color(0x6ec4ff), 0.015);
    } else {
      mat.color.lerp(new THREE.Color(0x4ca8e8), 0.015);
      lineMat.color.lerp(new THREE.Color(0x4ca8e8), 0.015);
    }

    /* ── Camera drift ── */
    camera.position.x = Math.sin(t * 0.02) * 5;
    camera.position.y = Math.cos(t * 0.03) * 3;
    camera.lookAt(0, 0, cloudZ * 0.2);

    renderer.render(scene, camera);
  }

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  window.addEventListener("resize", onResize);
  animate();

  return {
    setPhase(p: OrbPhase, onComplete?: () => void) {
      if (p === "scattering" && phase !== "scattering") {
        /* Capture scatter velocities from current positions */
        const a = (geo.getAttribute("position") as THREE.BufferAttribute)
          .array as Float32Array;
        for (let i = 0; i < N; i++) {
          const i3 = i * 3;
          const x = a[i3],
            y = a[i3 + 1],
            z = a[i3 + 2];
          const len = Math.sqrt(x * x + y * y + z * z) || 1;
          const spd = 0.08 + Math.random() * 0.12;
          scatVel[i3] = (x / len) * spd;
          scatVel[i3 + 1] = (y / len) * spd;
          scatVel[i3 + 2] = (z / len) * spd;
        }
        scatterT0 = clock.getElapsedTime();
        scatterFired = false;
        scatterOpacity = 1;
        scatterCallback = onComplete ?? null;
      }
      phase = p;
    },
    destroy() {
      destroyed = true;
      window.removeEventListener("resize", onResize);
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

/* ── React wrapper ── */
export default function ParticleIntro({ phase, onScatterComplete }: Props) {
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
    if (!canvasRef.current) return;
    orbRef.current = createOrb(canvasRef.current);
    return () => {
      orbRef.current?.destroy();
      orbRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!orbRef.current) return;
    if (phase === "scattering") {
      orbRef.current.setPhase("scattering", () => callbackRef.current());
    } else {
      orbRef.current.setPhase(phase);
    }
  }, [phase]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 3,
        width: "100%",
        height: "100%",
        opacity: visible ? 1 : 0,
        transition: "opacity 0.6s ease",
      }}
    />
  );
}
