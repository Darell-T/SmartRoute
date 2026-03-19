"use client"

import { useState } from "react"
import { JarvisMap } from "@/components/jarvis-map"
import { Settings, User, Zap, ArrowRight, AudioLines } from "lucide-react"

export default function JarvisPage() {
  const [inputValue, setInputValue] = useState("")

  return (
    <div className="relative h-screen w-full overflow-hidden bg-[#0a0a0f]">
      {/* Full-screen Mapbox 3D Map */}
      <JarvisMap />

      {/* JARVIS Logo - Top Left */}
      <div className="absolute top-6 left-6 z-10">
        <h1 className="text-xl font-bold tracking-wider text-[#4da6ff]">JARVIS</h1>
      </div>

      {/* Right Panel - Floating over map */}
      <div className="absolute top-4 right-4 bottom-24 w-[280px] z-10 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-sm font-semibold tracking-wider text-[#4da6ff]">JARVIS</h2>
            <p className="text-xs text-gray-500 tracking-widest">SYSTEM ACTIVE</p>
          </div>
          <div className="flex items-center gap-3">
            <button className="text-gray-400 hover:text-white transition-colors">
              <User size={20} />
            </button>
            <button className="text-gray-400 hover:text-white transition-colors">
              <Settings size={20} />
            </button>
          </div>
        </div>

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col gap-4">
          {/* Analysis Message */}
          <div className="space-y-3">
            <h3 className="text-lg text-white leading-relaxed">
              Analyzing route options for your trip to the airport, sir.
            </h3>
            <div className="w-8 h-px bg-[#4da6ff]/50" />
            <p className="text-sm text-gray-400 leading-relaxed">
              Traffic is currently moderate on US-101. Recommending the Express Shuttle for a 08:45 arrival.
            </p>
          </div>

          {/* Transit Card */}
          <div className="bg-[#12121a]/80 backdrop-blur-sm rounded-lg p-4 border border-gray-800/50">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-500 tracking-wider">NEXT TRANSIT</span>
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-[#4da6ff] animate-pulse" />
                <span className="text-xs text-[#4da6ff]">LIVE</span>
              </div>
            </div>
            <div className="text-3xl font-light text-white mb-1">08:12</div>
            <div className="text-xs text-gray-400 tracking-wider">LINE 42 • NORTHBOUND</div>
          </div>

          {/* ETA Card */}
          <div className="bg-[#12121a]/80 backdrop-blur-sm rounded-lg p-4 border border-gray-800/50">
            <span className="text-xs text-gray-500 tracking-wider">ETA</span>
            <div className="text-3xl font-light text-white mt-1">24 min</div>
            <div className="mt-3 h-1 bg-gray-800 rounded-full overflow-hidden">
              <div className="h-full w-2/3 bg-gradient-to-r from-[#4da6ff] to-[#2d7dd2] rounded-full" />
            </div>
          </div>

          {/* Overview Section */}
          <div className="flex items-center gap-2 mt-2">
            <div className="w-2 h-2 rounded-full bg-[#4da6ff]" />
            <span className="text-sm text-[#4da6ff] tracking-wider">OVERVIEW</span>
            <div className="ml-auto w-0.5 h-4 bg-[#4da6ff]" />
          </div>
        </div>
      </div>

      {/* Input Bar - Bottom Center */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 w-full max-w-xl px-4">
        <div className="flex items-center gap-3 bg-[#12121a]/90 backdrop-blur-md rounded-full px-5 py-3 border border-gray-800/50">
          <AudioLines className="text-[#4da6ff] shrink-0" size={20} />
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Where are you headed, sir?"
            className="flex-1 bg-transparent text-white placeholder-gray-500 outline-none text-sm"
          />
          <button className="w-9 h-9 rounded-lg bg-[#4da6ff] flex items-center justify-center hover:bg-[#3d96ef] transition-colors">
            <ArrowRight className="text-white" size={18} />
          </button>
        </div>
      </div>

      {/* AI Core Alpha - Bottom Right */}
      <div className="absolute bottom-8 right-6 z-10 flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-[#12121a]/80 backdrop-blur-sm border border-gray-800/50 flex items-center justify-center">
          <Zap className="text-[#4da6ff]" size={18} />
        </div>
        <div>
          <div className="text-xs font-medium text-white tracking-wider">AI CORE ALPHA</div>
          <div className="text-xs text-gray-500">Latency: 12ms</div>
        </div>
      </div>
    </div>
  )
}
