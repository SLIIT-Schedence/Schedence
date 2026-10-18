"use client";

import Image from "next/image";
import { type FocusEvent, useState, useSyncExternalStore } from "react";
import { Wordmark } from "@/components/wordmark";

const SLIDES = [
  {
    src: "/Events-pana.svg",
    title: "Your semester, scheduled.",
    text: "Teaching timetables, exam schedules and venues for your institution, in one place.",
  },
  {
    src: "/Reminders-rafiki.svg",
    title: "Always know what's next.",
    text: "See your upcoming lectures, exams and venues at a glance.",
  },
];

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function subscribeToMotion(onChange: () => void) {
  const query = window.matchMedia(REDUCED_MOTION);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

const prefersReducedMotion = () => window.matchMedia(REDUCED_MOTION).matches;
// The server can't know, so it renders the still version and the client starts the slideshow.
const reducedMotionOnServer = () => true;

/**
 * The login page's brand side: the two illustrations as an auto-advancing slideshow with a caption each.
 * The active progress bar's animation drives the timer, so pausing it (button, hover or keyboard focus)
 * pauses the slideshow. With reduced motion the slideshow never advances on its own.
 */
export function LoginShowcase() {
  const [slide, setSlide] = useState<{ index: number; previous: number | null; direction: 1 | -1 }>({
    index: 0,
    previous: null,
    direction: 1,
  });
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const reducedMotion = useSyncExternalStore(subscribeToMotion, prefersReducedMotion, reducedMotionOnServer);
  const running = !reducedMotion && !paused && !hovered && !focused;

  const advance = () =>
    setSlide(({ index }) => ({ index: (index + 1) % SLIDES.length, previous: index, direction: 1 }));
  const show = (index: number) => {
    if (index === slide.index) return;
    setSlide({ index, previous: slide.index, direction: index > slide.index ? 1 : -1 });
  };

  // Only keyboard focus holds the slideshow; a mouse click on a control shouldn't stop it for good.
  const onFocus = (event: FocusEvent<HTMLElement>) => setFocused(event.target.matches(":focus-visible"));
  const onBlur = (event: FocusEvent<HTMLElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
  };

  return (
    <section
      aria-roledescription="carousel"
      aria-label="About Schedence"
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onFocus={onFocus}
      onBlur={onBlur}
      className="flex flex-col overflow-hidden lg:sticky lg:top-0 lg:h-dvh lg:self-start"
    >
      <div className="px-4 pt-6 sm:px-8 lg:px-14 lg:pt-12">
        <Wordmark />
      </div>

      <div
        aria-live={running ? "off" : "polite"}
        className={`grid min-h-0 flex-1 grid-rows-1 px-4 sm:px-8 lg:px-14 ${
          slide.direction === 1 ? "[--slide-shift:2.5rem]" : "[--slide-shift:-2.5rem]"
        }`}
      >
        {SLIDES.map((item, index) => {
          const isActive = index === slide.index;
          const motion = isActive ? "animate-slide-in" : index === slide.previous ? "animate-slide-out" : "opacity-0";
          return (
            <div
              key={item.src}
              role="group"
              aria-roledescription="slide"
              aria-label={`${index + 1} of ${SLIDES.length}`}
              aria-hidden={!isActive}
              className="flex min-h-0 flex-col [grid-area:1/1]"
            >
              <div className={`relative my-5 h-52 sm:h-64 lg:my-8 lg:h-auto lg:min-h-0 lg:flex-1 ${motion}`}>
                <Image
                  src={item.src}
                  alt=""
                  fill
                  sizes="(min-width: 1024px) 50vw, 100vw"
                  loading="eager"
                  fetchPriority={index === 0 ? "high" : "auto"}
                  className="object-contain"
                />
              </div>
              <div className={`${motion} ${isActive ? "[animation-delay:120ms]" : ""}`}>
                <p className="max-w-[16ch] text-balance text-[1.75rem] font-semibold leading-[1.06] tracking-tight text-ink font-stretch-expanded lg:text-[clamp(2rem,2.9vw,2.75rem)]">
                  {item.title}
                </p>
                <p className="mt-3 max-w-[46ch] text-[15px] leading-6 text-muted">{item.text}</p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-4 px-4 pb-2 pt-6 sm:px-8 lg:px-14 lg:pb-12 lg:pt-8">
        <div className="flex gap-2">
          {SLIDES.map((item, index) => (
            <button
              key={item.src}
              type="button"
              onClick={() => show(index)}
              aria-label={`Show slide ${index + 1}: ${item.title}`}
              aria-current={index === slide.index}
              className="group flex h-6 w-14 items-center"
            >
              <span className="relative h-1 w-full overflow-hidden rounded-full bg-line transition-colors group-hover:bg-secondary/40">
                {index === slide.index && (
                  <span
                    onAnimationEnd={advance}
                    style={{ animationPlayState: running ? "running" : "paused" }}
                    className={`absolute inset-0 origin-left rounded-full bg-primary ${
                      reducedMotion ? "" : "animate-slide-progress"
                    }`}
                  />
                )}
              </span>
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setPaused((value) => !value)}
          aria-label={paused ? "Play slideshow" : "Pause slideshow"}
          className={`grid size-9 shrink-0 place-items-center rounded-full border border-line bg-surface text-muted transition-colors hover:border-primary/40 hover:text-primary-strong ${
            reducedMotion ? "invisible" : ""
          }`}
        >
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="size-4">
            {paused ? (
              <path d="M6.3 2.84A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.3 1.27l9.34-5.89a1.5 1.5 0 0 0 0-2.54L6.3 2.84Z" />
            ) : (
              <path d="M5.75 3a.75.75 0 0 0-.75.75v12.5c0 .41.34.75.75.75h1.5a.75.75 0 0 0 .75-.75V3.75A.75.75 0 0 0 7.25 3h-1.5ZM12.75 3a.75.75 0 0 0-.75.75v12.5c0 .41.34.75.75.75h1.5a.75.75 0 0 0 .75-.75V3.75a.75.75 0 0 0-.75-.75h-1.5Z" />
            )}
          </svg>
        </button>
      </div>
    </section>
  );
}
