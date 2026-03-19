"use client"

import { useEffect, useRef, useState } from "react"
import mapboxgl from "mapbox-gl"
import "mapbox-gl/dist/mapbox-gl.css"

interface JarvisMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number }) => void
}

export function JarvisMap({ onLocationUpdate }: JarvisMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null)
  const map = useRef<mapboxgl.Map | null>(null)
  const markerElement = useRef<HTMLDivElement | null>(null)
  const marker = useRef<mapboxgl.Marker | null>(null)
  const [userLocation, setUserLocation] = useState<{ lng: number; lat: number } | null>(null)

  useEffect(() => {
    if (!mapContainer.current) return

    mapboxgl.accessToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN || ""

    // Default to San Francisco if no GPS
    const defaultLocation = { lng: -122.4194, lat: 37.7749 }

    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: "mapbox://styles/mapbox/dark-v11",
      center: [defaultLocation.lng, defaultLocation.lat],
      zoom: 16,
      pitch: 55,
      bearing: -17.6,
      antialias: true,
    })

    map.current.on("style.load", () => {
      if (!map.current) return

      // Enable 3D buildings
      const layers = map.current.getStyle().layers
      const labelLayerId = layers?.find(
        (layer) => layer.type === "symbol" && layer.layout?.["text-field"]
      )?.id

      map.current.addLayer(
        {
          id: "3d-buildings",
          source: "composite",
          "source-layer": "building",
          filter: ["==", "extrude", "true"],
          type: "fill-extrusion",
          minzoom: 15,
          paint: {
            "fill-extrusion-color": "#1a1a2e",
            "fill-extrusion-height": ["get", "height"],
            "fill-extrusion-base": ["get", "min_height"],
            "fill-extrusion-opacity": 0.8,
          },
        },
        labelLayerId
      )

      // Create the glowing orb marker
      createOrbMarker(defaultLocation)
    })

    // Get user's GPS location
    if (navigator.geolocation) {
      navigator.geolocation.watchPosition(
        (position) => {
          const coords = {
            lng: position.coords.longitude,
            lat: position.coords.latitude,
          }
          setUserLocation(coords)
          onLocationUpdate?.(coords)

          if (map.current) {
            map.current.flyTo({
              center: [coords.lng, coords.lat],
              zoom: 16,
              pitch: 55,
              duration: 2000,
            })

            // Update marker position
            if (marker.current) {
              marker.current.setLngLat([coords.lng, coords.lat])
            } else {
              createOrbMarker(coords)
            }
          }
        },
        (error) => {
          console.log("Geolocation error:", error.message)
        },
        {
          enableHighAccuracy: true,
          maximumAge: 10000,
          timeout: 5000,
        }
      )
    }

    function createOrbMarker(coords: { lng: number; lat: number }) {
      if (!map.current) return

      // Create custom orb element
      const el = document.createElement("div")
      el.className = "jarvis-orb"
      el.innerHTML = `
        <div class="orb-core"></div>
        <div class="orb-glow"></div>
        <div class="orb-pulse"></div>
      `
      markerElement.current = el

      marker.current = new mapboxgl.Marker({
        element: el,
        anchor: "center",
      })
        .setLngLat([coords.lng, coords.lat])
        .addTo(map.current)
    }

    // Disable user interaction to lock to GPS
    map.current.scrollZoom.disable()
    map.current.boxZoom.disable()
    map.current.dragRotate.disable()
    map.current.dragPan.disable()
    map.current.keyboard.disable()
    map.current.doubleClickZoom.disable()
    map.current.touchZoomRotate.disable()

    return () => {
      map.current?.remove()
    }
  }, [onLocationUpdate])

  return (
    <>
      <style jsx global>{`
        .jarvis-orb {
          width: 80px;
          height: 80px;
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .orb-core {
          width: 20px;
          height: 20px;
          background: radial-gradient(circle, #4da6ff 0%, #2d7dd2 50%, #1a5fa8 100%);
          border-radius: 50%;
          position: absolute;
          z-index: 3;
          box-shadow: 0 0 10px #4da6ff, 0 0 20px #4da6ff, 0 0 30px #2d7dd2;
        }

        .orb-glow {
          width: 60px;
          height: 60px;
          background: radial-gradient(circle, rgba(77, 166, 255, 0.3) 0%, rgba(45, 125, 210, 0.1) 50%, transparent 70%);
          border-radius: 50%;
          position: absolute;
          z-index: 2;
          animation: orbGlow 2s ease-in-out infinite;
        }

        .orb-pulse {
          width: 80px;
          height: 80px;
          background: radial-gradient(circle, rgba(77, 166, 255, 0.15) 0%, transparent 60%);
          border-radius: 50%;
          position: absolute;
          z-index: 1;
          animation: orbPulse 3s ease-in-out infinite;
        }

        @keyframes orbGlow {
          0%, 100% {
            transform: scale(1);
            opacity: 1;
          }
          50% {
            transform: scale(1.1);
            opacity: 0.8;
          }
        }

        @keyframes orbPulse {
          0%, 100% {
            transform: scale(1);
            opacity: 0.6;
          }
          50% {
            transform: scale(1.3);
            opacity: 0.3;
          }
        }
      `}</style>
      <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
    </>
  )
}
