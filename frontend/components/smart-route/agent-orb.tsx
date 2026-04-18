"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export type AgentOrbPhase = "idle" | "thinking" | "speaking";

interface Props {
  phase: AgentOrbPhase;
  size?: number;
  accent?: string;
}

// Compact three.js particle core for the recommendation card — no scatter.
export function AgentOrb({ phase, size = 72, accent = "#d4a7ff" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef<AgentOrbPhase>(phase);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const N = 420;
    const MAX_LINES = 800;

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
    const idleColor = new THREE.Color(0x4ca8e8);

    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(N * 3);
    const vel = new Float32Array(N * 3);
    const pPhase = new Float32Array(N);

    for (let i = 0; i < N; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = Math.pow(Math.random(), 0.5) * 6;
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      pPhase[i] = Math.random() * 1000;
    }
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

    const mat = new THREE.PointsMaterial({
      color: idleColor.clone(),
      size: 0.18,
      transparent: true,
      opacity: 0.75,
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
    let targetRadius = 7,
      currentRadius = 7;
    let targetSpeed = 0.25,
      currentSpeed = 0.25;
    let targetBright = 0.55,
      currentBright = 0.55;
    let targetSize = 0.2,
      currentSize = 0.2;
    let lineAmount = 0,
      targetLineAmount = 0;

    const clock = new THREE.Clock();
    let lastTime = 0;
    const lineStep = Math.max(1, Math.floor(N / 180));
    const lineDistance = 2.2;

    function animate() {
      if (destroyed) return;
      requestAnimationFrame(animate);

      const t = clock.getElapsedTime();
      const dt = Math.min(t - lastTime, 0.05);
      lastTime = t;

      const p = phaseRef.current;
      if (p === "idle") {
        targetRadius = 7;
        targetSpeed = 0.25;
        targetBright = 0.55;
        targetSize = 0.2;
        targetLineAmount = 0.1;
        mat.color.lerp(idleColor, 0.04);
        lineMat.color.lerp(idleColor, 0.04);
      } else if (p === "thinking") {
        targetRadius = 4.2;
        targetSpeed = 0.55;
        targetBright = 0.9;
        targetSize = 0.22;
        targetLineAmount = 1.0;
        mat.color.lerp(accentColor, 0.04);
        lineMat.color.lerp(accentColor, 0.04);
      } else {
        // speaking — pulsing bright orb, fewer lines
        const pulse = 0.85 + Math.sin(t * 6) * 0.15;
        targetRadius = 5.5 + Math.sin(t * 4) * 0.5;
        targetSpeed = 0.35;
        targetBright = pulse;
        targetSize = 0.26;
        targetLineAmount = 0.4;
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
        const pull = Math.max(0, dist - currentRadius) * 0.005 + 0.0008;
        vel[i3] -= (x / dist) * pull;
        vel[i3 + 1] -= (y / dist) * pull;
        vel[i3 + 2] -= (z / dist) * pull;

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
        lineMat.opacity = lineAmount * 0.35;
      } else {
        lineGeo.setDrawRange(0, 0);
        lineMat.opacity = 0;
      }

      renderer.render(scene, camera);
      void dt;
    }

    animate();

    return () => {
      destroyed = true;
      geo.dispose();
      mat.dispose();
      lineGeo.dispose();
      lineMat.dispose();
      renderer.dispose();
    };
  }, [size, accent]);

  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      style={{ width: size, height: size, display: "block" }}
    />
  );
}
