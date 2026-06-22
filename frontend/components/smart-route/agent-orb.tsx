"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export type AgentOrbPhase = "idle" | "thinking" | "speaking";

interface Props {
  phase: AgentOrbPhase;
  size?: number;
  accent?: string;
  // Resting particle color (idle phase). Defaults to the legacy blue so
  // existing consumers are unchanged; the liquid-glass rail passes a warm
  // gold so the standby orb stays on-palette.
  idleAccent?: string;
}

// three.js particle core: a SCATTERED field at rest (idle), coalescing into a
// connected sphere once ATLAS comes alive (thinking / speaking).
export function AgentOrb({ phase, size = 72, accent = "#d4a7ff", idleAccent = "#4ca8e8" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef<AgentOrbPhase>(phase);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const N = 520;
    const MAX_LINES = 1050;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(size, size, false);
    renderer.setClearColor(0x000000, 0);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 20;

    const accentColor = new THREE.Color(accent);
    const idleColor = new THREE.Color(idleAccent);

    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(N * 3);
    const vel = new Float32Array(N * 3);
    const pPhase = new Float32Array(N);

    for (let i = 0; i < N; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = Math.pow(Math.random(), 0.32) * 7.2;
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      pPhase[i] = Math.random() * 1000;
    }
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

    const mat = new THREE.PointsMaterial({
      color: idleColor.clone(),
      size: 0.34,
      transparent: true,
      opacity: 0.92,
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
      color: accentColor.clone(),
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const lines = new THREE.LineSegments(lineGeo, lineMat);
    scene.add(lines);

    let destroyed = false;
    let targetRadius = 7.15,
      currentRadius = 7.15;
    let targetSpeed = 0.14,
      currentSpeed = 0.14;
    let targetBright = 0.9,
      currentBright = 0.9;
    let targetSize = 0.4,
      currentSize = 0.4;
    let lineAmount = 0,
      targetLineAmount = 0;

    const clock = new THREE.Clock();
    let lastTime = 0;
    const lineStep = Math.max(1, Math.floor(N / 180));
    const lineDistance = 2.45;

    let rafId = 0;
    function animate() {
      if (destroyed) return;
      rafId = requestAnimationFrame(animate);

      const t = clock.getElapsedTime();
      const dt = Math.min(t - lastTime, 0.05);
      lastTime = t;

      const p = phaseRef.current;
      if (p === "idle") {
        // Scattered, dormant field: a wide loose cloud drifting in random
        // motion with NO connecting lines and no spherical shell. ATLAS only
        // coalesces into the sphere once route planning begins.
        targetRadius = 9.6;
        targetSpeed = 0.55;
        targetBright = 0.85;
        targetSize = 0.36;
        targetLineAmount = 0;
        mat.color.lerp(idleColor, 0.04);
        lineMat.color.lerp(idleColor, 0.04);
      } else if (p === "thinking") {
        targetRadius = 5.45;
        targetSpeed = 0.46;
        targetBright = 1;
        targetSize = 0.42;
        targetLineAmount = 0.72;
        mat.color.lerp(accentColor, 0.04);
        lineMat.color.lerp(accentColor, 0.04);
      } else {
        // speaking — compact glow with a subtle breath, not a zooming collapse
        const pulse = 0.9 + Math.sin(t * 5.2) * 0.08;
        targetRadius = 6.1 + Math.sin(t * 3.2) * 0.16;
        targetSpeed = 0.3;
        targetBright = Math.max(0.88, pulse);
        targetSize = 0.39;
        targetLineAmount = 0.38;
        mat.color.lerp(accentColor, 0.04);
        lineMat.color.lerp(accentColor, 0.04);
      }

      currentRadius += (targetRadius - currentRadius) * 0.04;
      currentSpeed += (targetSpeed - currentSpeed) * 0.04;
      currentBright += (targetBright - currentBright) * 0.06;
      currentSize += (targetSize - currentSize) * 0.04;
      lineAmount += (targetLineAmount - lineAmount) * 0.04;

      points.rotation.y += 0.004;
      points.rotation.x += 0.0015;
      lines.rotation.y = points.rotation.y;
      lines.rotation.x = points.rotation.x;

      const posAttr = geo.getAttribute("position") as THREE.BufferAttribute;
      const a = posAttr.array as Float32Array;
      for (let i = 0; i < N; i++) {
        const i3 = i * 3;
        let x = a[i3],
          y = a[i3 + 1],
          z = a[i3 + 2];
        const px = pPhase[i];

        vel[i3] += Math.sin(t * 0.08 + px) * 0.001 * currentSpeed;
        vel[i3 + 1] += Math.cos(t * 0.09 + px * 1.3) * 0.001 * currentSpeed;
        vel[i3 + 2] += Math.sin(t * 0.07 + px * 0.7) * 0.001 * currentSpeed;

        const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
        const nx = x / dist;
        const ny = y / dist;
        const nz = z / dist;
        // No inner shell when idle -- particles fill the volume loosely
        // (scattered), rather than being pushed onto a sphere surface.
        const innerRadius = currentRadius * (p === "idle" ? 0 : 0.58);
        if (dist > currentRadius) {
          const pull = (dist - currentRadius) * 0.0045;
          vel[i3] -= nx * pull;
          vel[i3 + 1] -= ny * pull;
          vel[i3 + 2] -= nz * pull;
        } else if (dist < innerRadius) {
          const push = (innerRadius - dist) * 0.0038 + 0.0005;
          vel[i3] += nx * push;
          vel[i3 + 1] += ny * push;
          vel[i3 + 2] += nz * push;
        }

        vel[i3] *= 0.988;
        vel[i3 + 1] *= 0.988;
        vel[i3 + 2] *= 0.988;

        a[i3] += vel[i3];
        a[i3 + 1] += vel[i3 + 1];
        a[i3 + 2] += vel[i3 + 2];
      }
      posAttr.needsUpdate = true;

      mat.opacity = currentBright;
      mat.size = currentSize;

      if (lineAmount > 0.02) {
        const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
        const la = lp.array as Float32Array;
        let count = 0;
        const maxDistSq = lineDistance * lineDistance;
        for (let i = 0; i < N && count < MAX_LINES; i += lineStep) {
          const i3 = i * 3;
          for (
            let j = i + lineStep;
            j < N && count < MAX_LINES;
            j += lineStep
          ) {
            const j3 = j * 3;
            const dx = a[j3] - a[i3],
              dy = a[j3 + 1] - a[i3 + 1],
              dz = a[j3 + 2] - a[i3 + 2];
            if (dx * dx + dy * dy + dz * dz < maxDistSq) {
              const idx = count * 6;
              la[idx] = a[i3];
              la[idx + 1] = a[i3 + 1];
              la[idx + 2] = a[i3 + 2];
              la[idx + 3] = a[j3];
              la[idx + 4] = a[j3 + 1];
              la[idx + 5] = a[j3 + 2];
              count++;
            }
          }
        }
        lineGeo.setDrawRange(0, count * 2);
        lp.needsUpdate = true;
        lineMat.opacity = Math.min(0.62, lineAmount * 0.5);
      } else {
        lineGeo.setDrawRange(0, 0);
        lineMat.opacity = 0;
      }

      renderer.render(scene, camera);
      void dt;
    }

    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function onVisibility() {
      if (document.hidden) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      } else if (!destroyed && !reduceMotion && rafId === 0) {
        lastTime = clock.getElapsedTime();
        rafId = requestAnimationFrame(animate);
      }
    }
    document.addEventListener("visibilitychange", onVisibility);

    if (reduceMotion) {
      // Honor the reduced-motion preference: render one static frame instead of
      // the continuous particle simulation.
      renderer.render(scene, camera);
    } else {
      animate();
    }

    return () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      document.removeEventListener("visibilitychange", onVisibility);
      geo.dispose();
      mat.dispose();
      lineGeo.dispose();
      lineMat.dispose();
      renderer.dispose();
    };
  }, [size, accent, idleAccent]);

  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      style={{
        width: size,
        height: size,
        display: "block",
        position: "relative",
        zIndex: 1,
      }}
    />
  );
}
