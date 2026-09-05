/*
 * Formal-page feedback effects extracted from the approved feedback demo.
 * Stable integration contract: create({ stage, canvas, onResult }) returns
 * { play(type), stop(), resize(), destroy() }.
 */
(() => {
  const EFFECT_DEFINITIONS = Object.freeze({
    correct: Object.freeze({
      title: "回答正确",
      hint: "轻量确认：一簇金色与青绿色星光快速绽放。",
      score: "+1 分",
      icon: "✓",
      duration: 620,
      palette: ["#e7b35a", "#25875e", "#fff2ba", "#6db8a1"],
    }),
    "super-correct": Object.freeze({
      title: "超级正确",
      hint: "连续表现奖励：三段错峰烟花与光环，明显强于普通正确。",
      score: "+2 分",
      icon: "★",
      duration: 960,
      palette: ["#e7b35a", "#f6d778", "#1f8c8f", "#fff8d9", "#78c8b2"],
    }),
    wrong: Object.freeze({
      title: "回答错误",
      hint: "短促提示：橙红冲击点与少量余烬，不打断下一题。",
      score: "−1 分",
      icon: "×",
      duration: 480,
      palette: ["#c25a50", "#e27d58", "#f0b15a", "#fff0d5"],
    }),
    "super-wrong": Object.freeze({
      title: "超级错误",
      hint: "连续错误提醒：主冲击环、次级碎屑与极轻微卡片抖动。",
      score: "−2 分",
      icon: "!",
      duration: 760,
      palette: ["#ba4d39", "#e27d58", "#f2a64f", "#ffe0b0", "#fff5ea"],
    }),
  });

  const random = (minimum, maximum) => minimum + Math.random() * (maximum - minimum);
  const easeOut = (value) => 1 - ((1 - value) ** 2);
  const isReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Public integration seam: the formal quiz page only needs one stage,
  // one canvas, and controller.play(resultType).
  const createFeedbackEffects = ({ stage, canvas, onResult = () => {} } = {}) => {
    if (!(stage instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement)) {
      throw new TypeError("反馈动效需要传入 stage HTMLElement 和 canvas HTMLCanvasElement。");
    }

    const context = canvas.getContext("2d");
            if (!context) {
              return Object.freeze({
                play(type) {
                  const config = EFFECT_DEFINITIONS[type];
                  if (!config) return false;
                  onResult(type, config);
                  return true;
                },
                stop() {},
                resize() {},
                destroy() {},
              });
            }
    const state = {
      width: 0,
      height: 0,
      ratio: 1,
      frame: 0,
      startedAt: 0,
      lastFrame: 0,
      duration: 0,
      particles: [],
      rings: [],
      flashes: [],
    };

    const resize = () => {
      const bounds = stage.getBoundingClientRect();
      state.width = Math.max(1, bounds.width);
      state.height = Math.max(1, bounds.height);
      state.ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(state.width * state.ratio);
      canvas.height = Math.round(state.height * state.ratio);
      context.setTransform(state.ratio, 0, 0, state.ratio, 0, 0);
    };

    const stop = () => {
      if (state.frame) window.cancelAnimationFrame(state.frame);
      state.frame = 0;
      state.lastFrame = 0;
      state.particles = [];
      state.rings = [];
      state.flashes = [];
      context.globalAlpha = 1;
      context.clearRect(0, 0, state.width, state.height);
    };

    const addRing = (x, y, color, maxRadius, duration, lineWidth = 1.5, delay = 0) => {
      state.rings.push({ x, y, color, maxRadius, duration, lineWidth, age: -delay });
    };

    const addFlash = (x, y, color, maxRadius, duration, alpha = 0.2, delay = 0) => {
      state.flashes.push({ x, y, color, maxRadius, duration, alpha, age: -delay });
    };

    const addSparkBurst = ({
      x,
      y,
      palette,
      count = 24,
      speedMin = 1.15,
      speedMax = 2.45,
      gravityMin = 0.014,
      gravityMax = 0.035,
      sizeMin = 1.4,
      sizeMax = 3,
      durationMin = 360,
      durationMax = 650,
      scale = 1,
      delay = 0,
      starEvery = 0,
    }) => {
      for (let index = 0; index < count; index += 1) {
        const angle = (Math.PI * 2 * index) / count + random(-0.1, 0.1);
        const speed = random(speedMin, speedMax) * scale;
        state.particles.push({
          type: starEvery && index % starEvery === 0 ? "star" : "spark",
          x,
          y,
          previousX: x,
          previousY: y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          gravity: random(gravityMin, gravityMax),
          drag: random(0.976, 0.992),
          age: -delay,
          duration: random(durationMin, durationMax),
          size: random(sizeMin, sizeMax) * scale,
          rotation: random(0, Math.PI * 2),
          rotationSpeed: random(-0.08, 0.08),
          color: palette[index % palette.length],
        });
      }
    };

    const addExplosion = ({
      x,
      y,
      palette,
      count = 20,
      scale = 1,
      delay = 0,
      speedMin = 1.0,
      speedMax = 2.65,
      durationMin = 240,
      durationMax = 520,
      chunkEvery = 3,
    }) => {
      for (let index = 0; index < count; index += 1) {
        const angle = random(0, Math.PI * 2);
        const speed = random(speedMin, speedMax) * scale;
        state.particles.push({
          type: index % chunkEvery === 0 ? "chunk" : "ember",
          x,
          y,
          previousX: x,
          previousY: y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          gravity: random(0.035, 0.07),
          drag: random(0.96, 0.985),
          age: -delay,
          duration: random(durationMin, durationMax),
          size: random(1.9, 4.2) * scale,
          rotation: random(0, Math.PI * 2),
          rotationSpeed: random(-0.13, 0.13),
          color: palette[index % palette.length],
        });
      }
    };

    const drawStar = (particle) => {
      context.save();
      context.translate(particle.x, particle.y);
      context.rotate(particle.rotation);
      context.beginPath();
      for (let point = 0; point < 8; point += 1) {
        const angle = (Math.PI * 2 * point) / 8;
        const radius = point % 2 === 0 ? particle.size * 1.7 : particle.size * 0.5;
        const px = Math.cos(angle) * radius;
        const py = Math.sin(angle) * radius;
        if (point === 0) context.moveTo(px, py);
        else context.lineTo(px, py);
      }
      context.closePath();
      context.fill();
      context.restore();
    };

    const drawParticle = (particle) => {
      if (particle.age < 0) return;
      const progress = Math.min(1, particle.age / particle.duration);
      context.globalAlpha = Math.max(0, 1 - progress);
      context.strokeStyle = particle.color;
      context.fillStyle = particle.color;
      context.lineCap = "round";

      if (particle.type === "star") {
        drawStar(particle);
        return;
      }

      if (particle.type === "spark") {
        context.lineWidth = Math.max(1, particle.size * 0.7);
        context.beginPath();
        context.moveTo(particle.previousX, particle.previousY);
        context.lineTo(particle.x, particle.y);
        context.stroke();
        context.beginPath();
        context.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
        context.fill();
        return;
      }

      if (particle.type === "chunk") {
        context.save();
        context.translate(particle.x, particle.y);
        context.rotate(particle.rotation);
        context.fillRect(-particle.size, -particle.size, particle.size * 1.65, particle.size * 1.65);
        context.restore();
        return;
      }

      context.lineWidth = Math.max(1, particle.size * 0.62);
      context.beginPath();
      context.moveTo(particle.previousX, particle.previousY);
      context.lineTo(particle.x, particle.y);
      context.stroke();
      context.beginPath();
      context.arc(particle.x, particle.y, particle.size * 0.72, 0, Math.PI * 2);
      context.fill();
    };

    const drawRing = (ring) => {
      if (ring.age < 0) return;
      const progress = Math.min(1, ring.age / ring.duration);
      context.globalAlpha = Math.max(0, 1 - progress) * 0.56;
      context.strokeStyle = ring.color;
      context.lineWidth = ring.lineWidth;
      context.beginPath();
      context.arc(ring.x, ring.y, easeOut(progress) * ring.maxRadius, 0, Math.PI * 2);
      context.stroke();
    };

    const drawFlash = (flash) => {
      if (flash.age < 0) return;
      const progress = Math.min(1, flash.age / flash.duration);
      const radius = Math.max(1, easeOut(progress) * flash.maxRadius);
      const gradient = context.createRadialGradient(flash.x, flash.y, 0, flash.x, flash.y, radius);
      gradient.addColorStop(0, flash.color);
      gradient.addColorStop(1, "rgba(255,255,255,0)");
      context.globalAlpha = Math.sin(Math.PI * progress) * flash.alpha;
      context.fillStyle = gradient;
      context.beginPath();
      context.arc(flash.x, flash.y, radius, 0, Math.PI * 2);
      context.fill();
    };

    const animate = (now) => {
      const delta = Math.min(32, Math.max(0, now - (state.lastFrame || now)));
      state.lastFrame = now;
      const elapsed = now - state.startedAt;
      context.clearRect(0, 0, state.width, state.height);

      state.flashes = state.flashes.filter((flash) => {
        flash.age += delta;
        drawFlash(flash);
        return flash.age < flash.duration;
      });

      state.rings = state.rings.filter((ring) => {
        ring.age += delta;
        drawRing(ring);
        return ring.age < ring.duration;
      });

      state.particles = state.particles.filter((particle) => {
        particle.age += delta;
        if (particle.age < 0) return true;
        particle.previousX = particle.x;
        particle.previousY = particle.y;
        const frameScale = delta / 16.67;
        particle.x += particle.vx * frameScale;
        particle.y += particle.vy * frameScale;
        particle.vx *= particle.drag ** frameScale;
        particle.vy = particle.vy * (particle.drag ** frameScale) + particle.gravity * frameScale;
        particle.rotation += particle.rotationSpeed * frameScale;
        drawParticle(particle);
        return particle.age < particle.duration;
      });

      context.globalAlpha = 1;
      if (elapsed < state.duration || state.particles.length || state.rings.length || state.flashes.length) {
        state.frame = window.requestAnimationFrame(animate);
      } else {
        state.frame = 0;
        state.lastFrame = 0;
        context.clearRect(0, 0, state.width, state.height);
      }
    };

    const buildCorrect = (config) => {
      const x = state.width * 0.5;
      const y = state.height * 0.48;
      addFlash(x, y, "rgba(255, 242, 186, 0.95)", 58, 360, 0.26);
      addSparkBurst({
        x,
        y,
        palette: config.palette,
        count: 24,
        scale: 0.86,
        speedMin: 1.05,
        speedMax: 2.15,
        durationMin: 320,
        durationMax: 560,
        starEvery: 6,
      });
      addRing(x, y, config.palette[0], 34, 420, 1.35);
    };

    const buildSuperCorrect = (config) => {
      const points = [
        [state.width * 0.36, state.height * 0.52, 0],
        [state.width * 0.64, state.height * 0.57, 120],
        [state.width * 0.5, state.height * 0.34, 245],
      ];
      points.forEach(([x, y, delay], index) => {
        addFlash(x, y, index === 1 ? "rgba(120, 200, 178, 0.92)" : "rgba(255, 232, 154, 0.94)", 64, 390, 0.27, delay);
        addSparkBurst({
          x,
          y,
          palette: config.palette,
          count: index === 2 ? 17 : 27,
          scale: index === 2 ? 0.68 : 0.84,
          speedMin: 1.0,
          speedMax: index === 2 ? 1.95 : 2.38,
          durationMin: 340,
          durationMax: 650,
          delay,
          starEvery: 5,
        });
        addRing(x, y, config.palette[index % config.palette.length], index === 2 ? 28 : 39, 460, 1.45, delay);
      });
      addRing(state.width * 0.5, state.height * 0.52, "#f6d778", 68, 620, 1.1, 210);
    };

    const buildWrong = (config) => {
      const x = state.width * 0.5;
      const y = state.height * 0.53;
      addFlash(x, y, "rgba(240, 177, 90, 0.9)", 48, 260, 0.18);
      addExplosion({
        x,
        y,
        palette: config.palette,
        count: 17,
        scale: 0.78,
        speedMin: 0.85,
        speedMax: 2.0,
        durationMin: 220,
        durationMax: 430,
      });
      addRing(x, y, config.palette[0], 31, 320, 1.9);
    };

    const buildSuperWrong = (config) => {
      const x = state.width * 0.5;
      const y = state.height * 0.54;
      addFlash(x, y, "rgba(242, 166, 79, 0.96)", 68, 300, 0.23);
      addExplosion({
        x,
        y,
        palette: config.palette,
        count: 29,
        scale: 0.93,
        speedMin: 1.0,
        speedMax: 2.7,
        durationMin: 260,
        durationMax: 540,
      });
      addRing(x, y, config.palette[0], 45, 360, 2.2);
      addRing(x, y, config.palette[2], 73, 500, 1.35, 70);

      const sideX = state.width * 0.43;
      const sideY = state.height * 0.59;
      addExplosion({
        x: sideX,
        y: sideY,
        palette: config.palette,
        count: 13,
        scale: 0.56,
        delay: 135,
        speedMin: 0.8,
        speedMax: 1.9,
        durationMin: 220,
        durationMax: 420,
        chunkEvery: 4,
      });
      addFlash(sideX, sideY, "rgba(255, 224, 176, 0.9)", 38, 240, 0.15, 135);
    };

    const play = (type) => {
      const config = EFFECT_DEFINITIONS[type];
      if (!config) return false;

      stop();
      resize();
      onResult(type, config);
      if (isReducedMotion()) return true;

      state.duration = config.duration;
      state.startedAt = performance.now();
      state.lastFrame = state.startedAt;

      if (type === "correct") buildCorrect(config);
      else if (type === "super-correct") buildSuperCorrect(config);
      else if (type === "wrong") buildWrong(config);
      else buildSuperWrong(config);

      state.frame = window.requestAnimationFrame(animate);
      return true;
    };

    resize();
    const resizeObserver = window.ResizeObserver ? new ResizeObserver(resize) : null;
    resizeObserver?.observe(stage);
    window.addEventListener("resize", resize);

    return Object.freeze({
      play,
      stop,
      resize,
      destroy() {
        stop();
        resizeObserver?.disconnect();
        window.removeEventListener("resize", resize);
      },
    });
  };

  window.WenyanFeedbackEffects = Object.freeze({
    create: createFeedbackEffects,
    types: Object.freeze(Object.keys(EFFECT_DEFINITIONS)),
  });
})();
