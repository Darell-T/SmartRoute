"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export type IntroPhase = "idle" | "thinking" | "scattering";

interface Props {
  phase: IntroPhase;
  onScatterComplete: () => void;
}

const COUNT = 700;
const SPHERE_R = 1.5;
const CONVERGE_S = 1.5;
const SCATTER_S = 1.0;

/** Box-Muller Gaussian random (mean 0, std 1) */
function gaussRandom(): number {
  const u1 = Math.random() || 1e-10;
  const u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

export default function ParticleIntro({ phase, onScatterComplete }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({ phase, onScatterComplete });

  useEffect(() => {
    stateRef.current = { phase, onScatterComplete };
  }, [phase, onScatterComplete]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    /* Renderer */
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(innerWidth, innerHeight);
    renderer.setClearColor(0x020812);
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 100);
    cam.position.z = 8;

    /* Soft circular sprite texture */
    const spriteCanvas = document.createElement("canvas");
    spriteCanvas.width = spriteCanvas.height = 64;
    const sctx = spriteCanvas.getContext("2d")!;
    const sg = sctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    sg.addColorStop(0, "rgba(255,255,255,1)");
    sg.addColorStop(0.12, "rgba(255,255,255,0.9)");
    sg.addColorStop(0.35, "rgba(255,255,255,0.35)");
    sg.addColorStop(1, "rgba(255,255,255,0)");
    sctx.fillStyle = sg;
    sctx.fillRect(0, 0, 64, 64);
    const spriteTex = new THREE.CanvasTexture(spriteCanvas);

    /* Center glow sprite (large, soft) */
    const glowCanvas = document.createElement("canvas");
    glowCanvas.width = glowCanvas.height = 256;
    const gctx = glowCanvas.getContext("2d")!;
    const gg = gctx.createRadialGradient(128, 128, 0, 128, 128, 128);
    gg.addColorStop(0, "rgba(0, 212, 255, 0.8)");
    gg.addColorStop(0.12, "rgba(0, 190, 255, 0.5)");
    gg.addColorStop(0.35, "rgba(0, 120, 255, 0.15)");
    gg.addColorStop(0.65, "rgba(0, 60, 255, 0.04)");
    gg.addColorStop(1, "rgba(0, 0, 0, 0)");
    gctx.fillStyle = gg;
    gctx.fillRect(0, 0, 256, 256);
    const glowTex = new THREE.CanvasTexture(glowCanvas);
    const glowMat = new THREE.SpriteMaterial({
      map: glowTex,
      transparent: true,
      blending: THREE.AdditiveBlending,
      opacity: 0,
    });
    const glowSprite = new THREE.Sprite(glowMat);
    glowSprite.scale.set(5, 5, 1);
    scene.add(glowSprite);

    /* Particle buffers (GPU) */
    const pos = new Float32Array(COUNT * 3);
    const col = new Float32Array(COUNT * 3);
    const siz = new Float32Array(COUNT);
    const alp = new Float32Array(COUNT);

    /* Per-particle metadata (CPU) */
    // Idle: cylindrical orbit params
    const homeR = new Float32Array(COUNT);
    const homeTheta = new Float32Array(COUNT);
    const homeY = new Float32Array(COUNT);
    const orbitalSpd = new Float32Array(COUNT);
    const rOscPhase = new Float32Array(COUNT);
    const yOscPhase = new Float32Array(COUNT);
    const rOscFreq = new Float32Array(COUNT);
    const yOscFreq = new Float32Array(COUNT);
    // Thinking: sphere orbit params
    const sphPhi = new Float32Array(COUNT);
    const sphTheta0 = new Float32Array(COUNT);
    const sphRadius = new Float32Array(COUNT);
    const sphTarget = new Float32Array(COUNT * 3);
    const sphOrbSpd = new Float32Array(COUNT);
    // Common
    const baseSize = new Float32Array(COUNT);
    const baseAlpha = new Float32Array(COUNT);
    const scatVel = new Float32Array(COUNT * 3);
    const snapPos = new Float32Array(COUNT * 3);

    const cyan = new THREE.Color(0x00d4ff);
    const blue = new THREE.Color(0x0044ff);
    const brightCyan = new THREE.Color(0x88eeff);

    for (let i = 0; i < COUNT; i++) {
      const i3 = i * 3;

      /* Idle: Gaussian nebula distribution, concentrated at center */
      const gr = Math.abs(gaussRandom()) * 1.5;
      const r = Math.min(gr, 4);
      const theta = Math.random() * Math.PI * 2;
      const y = gaussRandom() * 1.0;

      homeR[i] = r;
      homeTheta[i] = theta;
      homeY[i] = y;

      pos[i3] = r * Math.cos(theta);
      pos[i3 + 1] = y;
      pos[i3 + 2] = r * Math.sin(theta);

      // Swirling orbit: each particle orbits Y-axis at its own speed + direction
      orbitalSpd[i] = (0.15 + Math.random() * 0.25) * (Math.random() < 0.5 ? 1 : -1);
      rOscPhase[i] = Math.random() * Math.PI * 2;
      yOscPhase[i] = Math.random() * Math.PI * 2;
      rOscFreq[i] = 0.3 + Math.random() * 0.4;
      yOscFreq[i] = 0.2 + Math.random() * 0.3;

      /* Color: center brighter, outer bluer */
      const distFactor = Math.min(r / 3, 1);
      const colorT = distFactor * 0.7 + Math.random() * 0.3;
      const c =
        r < 1 && Math.random() < 0.3
          ? brightCyan.clone().lerp(cyan, Math.random() * 0.5)
          : cyan.clone().lerp(blue, colorT);
      col[i3] = c.r;
      col[i3 + 1] = c.g;
      col[i3 + 2] = c.b;

      /* Size: 2-4px apparent, larger near center */
      baseSize[i] = 0.04 + Math.random() * 0.08 + (r < 1.5 ? 0.03 : 0);
      siz[i] = baseSize[i];

      /* Alpha: 0.5-0.9, brighter near center */
      baseAlpha[i] = 0.5 + Math.random() * 0.4 + (r < 1 ? 0.1 : 0);
      alp[i] = baseAlpha[i];

      /* Sphere target: 80% tight surface, 20% outer haze shell */
      const isHaze = Math.random() < 0.2;
      const sr = isHaze ? SPHERE_R + 0.3 + Math.random() * 0.5 : SPHERE_R;
      const sphi = Math.acos(2 * Math.random() - 1);
      const stheta = Math.random() * Math.PI * 2;

      sphPhi[i] = sphi;
      sphTheta0[i] = stheta;
      sphRadius[i] = sr;

      sphTarget[i3] = sr * Math.sin(sphi) * Math.cos(stheta);
      sphTarget[i3 + 1] = sr * Math.sin(sphi) * Math.sin(stheta);
      sphTarget[i3 + 2] = sr * Math.cos(sphi);

      // Inner particles orbit faster, haze particles drift slower
      sphOrbSpd[i] = isHaze ? 0.2 + Math.random() * 0.3 : 0.4 + Math.random() * 0.6;
    }

    /* Geometry + Shader Material */
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("customColor", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("size", new THREE.BufferAttribute(siz, 1));
    geo.setAttribute("alpha", new THREE.BufferAttribute(alp, 1));

    const mat = new THREE.ShaderMaterial({
      uniforms: { map: { value: spriteTex } },
      vertexShader: `
        attribute float size;
        attribute float alpha;
        attribute vec3 customColor;
        varying float vAlpha;
        varying vec3 vColor;
        void main() {
          vAlpha = alpha;
          vColor = customColor;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = size * (300.0 / -mv.z);
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: `
        uniform sampler2D map;
        varying float vAlpha;
        varying vec3 vColor;
        void main() {
          vec4 tex = texture2D(map, gl_PointCoord);
          gl_FragColor = vec4(vColor, vAlpha * tex.a);
        }
      `,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const points = new THREE.Points(geo, mat);
    scene.add(points);

    /* Animation state */
    let disposed = false;
    let prevPhase: IntroPhase = "idle";
    let convergeT0 = 0;
    let scatterT0 = 0;
    let scatterFired = false;
    const startTime = performance.now();
    let lastFrame = startTime;

    function animate() {
      if (disposed) return;
      requestAnimationFrame(animate);

      const now = performance.now();
      const dt = Math.min((now - lastFrame) / 1000, 0.05);
      lastFrame = now;
      const elapsed = (now - startTime) / 1000;
      const cur = stateRef.current.phase;

      /* Phase transitions */
      if (cur !== prevPhase) {
        if (cur === "thinking") {
          convergeT0 = elapsed;
          snapPos.set(pos);
        }
        if (cur === "scattering") {
          scatterT0 = elapsed;
          scatterFired = false;
          for (let i = 0; i < COUNT; i++) {
            const i3 = i * 3;
            const x = pos[i3], y = pos[i3 + 1], z = pos[i3 + 2];
            const len = Math.sqrt(x * x + y * y + z * z) || 1;
            const spd = 5 + Math.random() * 8;
            scatVel[i3] = (x / len) * spd;
            scatVel[i3 + 1] = (y / len) * spd;
            scatVel[i3 + 2] = (z / len) * spd;
          }
        }
        prevPhase = cur;
      }

      /* IDLE: swirling nebula cloud */
      if (cur === "idle") {
        for (let i = 0; i < COUNT; i++) {
          const i3 = i * 3;
          // Each particle orbits Y-axis with radial + vertical oscillation
          const theta = homeTheta[i] + elapsed * orbitalSpd[i];
          const r = homeR[i] + Math.sin(elapsed * rOscFreq[i] + rOscPhase[i]) * 0.2;
          const y = homeY[i] + Math.cos(elapsed * yOscFreq[i] + yOscPhase[i]) * 0.15;

          pos[i3] = r * Math.cos(theta);
          pos[i3 + 1] = y;
          pos[i3 + 2] = r * Math.sin(theta);

          alp[i] = baseAlpha[i];
          siz[i] = baseSize[i];
        }
        // Slow group rotation for organic feel
        points.rotation.y += 0.002;
        points.scale.setScalar(1);
        // Subtle idle glow from particle density
        glowMat.opacity = 0.15;
        glowSprite.scale.set(5, 5, 1);
      }

      /* THINKING: converge to dense sphere, orbit, pulse */
      else if (cur === "thinking") {
        const td = elapsed - convergeT0;
        const rawT = Math.min(td / CONVERGE_S, 1);
        const ease =
          rawT < 0.5
            ? 2 * rawT * rawT
            : 1 - Math.pow(-2 * rawT + 2, 2) / 2;

        for (let i = 0; i < COUNT; i++) {
          const i3 = i * 3;

          if (rawT < 1) {
            // Lerp from snapshot to sphere target
            pos[i3] = THREE.MathUtils.lerp(snapPos[i3], sphTarget[i3], ease);
            pos[i3 + 1] = THREE.MathUtils.lerp(snapPos[i3 + 1], sphTarget[i3 + 1], ease);
            pos[i3 + 2] = THREE.MathUtils.lerp(snapPos[i3 + 2], sphTarget[i3 + 2], ease);
          } else {
            // Orbit on sphere using stored phi/theta (avoids numerical issues)
            const phi = sphPhi[i];
            const theta = sphTheta0[i] + (td - CONVERGE_S) * sphOrbSpd[i];
            const r = sphRadius[i] + Math.sin(elapsed * 2.5 + i * 0.1) * 0.08;

            pos[i3] = r * Math.sin(phi) * Math.cos(theta);
            pos[i3 + 1] = r * Math.sin(phi) * Math.sin(theta);
            pos[i3 + 2] = r * Math.cos(phi);
          }

          // Brighten and enlarge as they converge
          alp[i] = THREE.MathUtils.lerp(
            baseAlpha[i],
            0.6 + Math.sin(elapsed * 4 + i) * 0.15,
            ease,
          );
          siz[i] = THREE.MathUtils.lerp(baseSize[i], baseSize[i] * 1.5, ease);
        }

        // Faster sphere rotation
        points.rotation.y += 0.008;

        // Pulsing scale (sphere breathes 0.95 - 1.05)
        const pulse = 0.95 + Math.sin(elapsed * 2) * 0.05;
        points.scale.setScalar(pulse);

        // Center glow intensifies with convergence
        const glowPulse = 0.7 + Math.sin(elapsed * 3) * 0.3;
        glowMat.opacity = THREE.MathUtils.lerp(0.15, 0.85 * glowPulse, ease);
        // Tighten glow to match sphere
        glowSprite.scale.setScalar(THREE.MathUtils.lerp(5, 3.5, ease));
      }

      /* SCATTERING: explode outward, fade to nothing */
      else if (cur === "scattering") {
        const td = elapsed - scatterT0;
        const t = Math.min(td / SCATTER_S, 1);

        for (let i = 0; i < COUNT; i++) {
          const i3 = i * 3;
          pos[i3] += scatVel[i3] * dt;
          pos[i3 + 1] += scatVel[i3 + 1] * dt;
          pos[i3 + 2] += scatVel[i3 + 2] * dt;
          alp[i] *= Math.pow(0.03, dt);
        }

        glowMat.opacity *= Math.pow(0.01, dt);
        points.rotation.y += 0.008;

        if (t >= 1 && !scatterFired) {
          scatterFired = true;
          stateRef.current.onScatterComplete();
        }
      }

      /* Flush GPU buffers */
      (geo.getAttribute("position") as THREE.BufferAttribute).needsUpdate = true;
      (geo.getAttribute("size") as THREE.BufferAttribute).needsUpdate = true;
      (geo.getAttribute("alpha") as THREE.BufferAttribute).needsUpdate = true;

      renderer.render(scene, cam);
    }

    animate();

    /* Resize */
    function onResize() {
      cam.aspect = innerWidth / innerHeight;
      cam.updateProjectionMatrix();
      renderer.setSize(innerWidth, innerHeight);
    }
    window.addEventListener("resize", onResize);

    /* Cleanup */
    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      geo.dispose();
      mat.dispose();
      spriteTex.dispose();
      glowTex.dispose();
      glowMat.dispose();
      renderer.dispose();
      if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement);
    };
  }, []);

  return <div ref={containerRef} style={{ position: "fixed", inset: 0, zIndex: 3 }} />;
}
