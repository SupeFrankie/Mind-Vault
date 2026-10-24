(() => {
  const root = document.getElementById("groupCall");
  if (!root) return;

  const csrf = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || "";
  const endpoint = (name) => root.dataset[name];
  const joinButton = document.getElementById("joinCall");
  const micButton = document.getElementById("toggleMic");
  const cameraButton = document.getElementById("toggleCamera");
  const leaveButton = document.getElementById("leaveCall");
  const status = document.getElementById("connectionStatus");
  const errorBox = document.getElementById("callError");
  const participantList = document.getElementById("participantList");
  const participantCount = document.getElementById("participantCount");
  const videoGrid = document.getElementById("videoGrid");
  const localVideo = document.getElementById("localVideo");
  const localPlaceholder = document.getElementById("localPlaceholder");
  const localName = document.getElementById("localName");

  let myId = null;
  let localStream = null;
  let audioMuted = false;
  let videoEnabled = false;
  let lastSignalId = 0;
  let pollTimer = null;
  let pollBusy = false;
  let joined = false;
  let leaving = false;
  const peers = new Map();
  const remoteTiles = new Map();

  async function post(url, data = {}) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || "The call server could not be reached.");
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    status.textContent = "Connection needs attention";
  }

  function participantName(id, roster) {
    return roster.find((person) => person.id === id)?.name || `Member ${id}`;
  }

  async function sendSignal(to, kind, payload) {
    await post(endpoint("signalUrl"), { to, kind, payload });
  }

  function makeRemoteTile(id, name) {
    let tile = remoteTiles.get(id);
    if (tile) {
      tile.querySelector(".call-video-name").textContent = name;
      return tile;
    }
    tile = document.createElement("article");
    tile.className = "call-video-tile";
    tile.dataset.participantId = String(id);
    const video = document.createElement("video");
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute("aria-label", `Video from ${name}`);
    const placeholder = document.createElement("div");
    placeholder.className = "call-video-placeholder";
    const initials = document.createElement("span");
    initials.setAttribute("aria-hidden", "true");
    initials.textContent = name.trim().slice(0, 2).toUpperCase() || "MV";
    const label = document.createElement("strong");
    label.textContent = "Camera is off";
    placeholder.append(initials, label);
    const nameLabel = document.createElement("span");
    nameLabel.className = "call-video-name";
    nameLabel.textContent = name;
    tile.append(video, placeholder, nameLabel);
    videoGrid.append(tile);
    remoteTiles.set(id, tile);
    return tile;
  }

  function setRemoteStream(id, stream) {
    const roster = window.callRoster || [];
    const tile = makeRemoteTile(id, participantName(id, roster));
    const video = tile.querySelector("video");
    const placeholder = tile.querySelector(".call-video-placeholder");
    video.srcObject = stream;
    const peer = roster.find((person) => person.id === id);
    const hasVideo = Boolean(peer?.video_enabled) && stream.getVideoTracks().some((track) => track.readyState === "live");
    placeholder.hidden = hasVideo;
    video.style.visibility = hasVideo ? "visible" : "hidden";
    video.play().catch(() => { status.textContent = "Tap the call to allow participant audio."; });
  }

  function closePeer(id) {
    peers.get(id)?.connection.close();
    peers.delete(id);
    remoteTiles.get(id)?.remove();
    remoteTiles.delete(id);
  }

  function ensurePeer(id, name) {
    if (peers.has(id)) return peers.get(id);
    const connection = new RTCPeerConnection({ iceServers: window.callIceServers || [] });
    const state = { connection, pendingCandidates: [] };
    peers.set(id, state);
    if (localStream) {
      localStream.getTracks().forEach((track) => connection.addTrack(track, localStream));
    }
    connection.ontrack = (event) => {
      const stream = event.streams[0] || new MediaStream([event.track]);
      setRemoteStream(id, stream);
    };
    connection.onicecandidate = (event) => {
      if (event.candidate) sendSignal(id, "candidate", event.candidate.toJSON()).catch(showError);
    };
    connection.onconnectionstatechange = () => {
      if (["failed", "disconnected", "closed"].includes(connection.connectionState)) {
        status.textContent = `Reconnecting to ${name}…`;
      }
    };
    return state;
  }

  async function flushCandidates(state) {
    for (const candidate of state.pendingCandidates.splice(0)) {
      await state.connection.addIceCandidate(candidate);
    }
  }

  async function handleSignal(signal, roster) {
    const id = Number(signal.sender);
    const name = participantName(id, roster);
    const state = ensurePeer(id, name);
    const pc = state.connection;
    if (signal.kind === "offer") {
      await pc.setRemoteDescription(signal.payload);
      await flushCandidates(state);
      await pc.setLocalDescription(await pc.createAnswer());
      await sendSignal(id, "answer", pc.localDescription.toJSON());
    } else if (signal.kind === "answer" && pc.signalingState === "have-local-offer") {
      await pc.setRemoteDescription(signal.payload);
      await flushCandidates(state);
    } else if (signal.kind === "candidate") {
      if (pc.remoteDescription) await pc.addIceCandidate(signal.payload);
      else state.pendingCandidates.push(signal.payload);
    }
  }

  async function refresh() {
    if (!joined || leaving || pollBusy) return;
    pollBusy = true;
    try {
      const result = await post(`${endpoint("pollUrl")}?after=${lastSignalId}`, {
        audio_muted: audioMuted,
        video_enabled: videoEnabled,
      });
      const roster = result.participants;
      window.callRoster = roster;
      const presentIds = new Set(roster.map((person) => person.id));
      participantCount.textContent = String(roster.length);
      participantList.replaceChildren();
      for (const person of roster) {
        const item = document.createElement("li");
        item.className = "call-person";
        const name = document.createElement("span");
        name.className = "call-person-name";
        name.textContent = person.id === myId ? `${person.name} (you)` : person.name;
        const detail = document.createElement("span");
        detail.className = "call-person-status";
        detail.textContent = `${person.audio_muted ? "Mic off" : "Mic on"} · ${person.video_enabled ? "Camera on" : "Camera off"}`;
        item.append(name, detail);
        participantList.append(item);
        if (person.id !== myId && remoteTiles.has(person.id)) {
          const tile = remoteTiles.get(person.id);
          tile.querySelector(".call-video-placeholder").hidden = person.video_enabled;
          tile.querySelector("video").style.visibility = person.video_enabled ? "visible" : "hidden";
        }
        if (person.id !== myId && myId > person.id) {
          const state = ensurePeer(person.id, person.name);
          if (state.connection.signalingState === "stable" && state.connection.connectionState !== "connected") {
            await state.connection.setLocalDescription(await state.connection.createOffer());
            await sendSignal(person.id, "offer", state.connection.localDescription.toJSON());
          }
        }
      }
      if (!roster.length) {
        const empty = document.createElement("li");
        empty.className = "call-people-empty";
        empty.textContent = "No one is connected yet.";
        participantList.append(empty);
      }
      for (const id of peers.keys()) if (!presentIds.has(id)) closePeer(id);
      for (const signal of result.signals) {
        lastSignalId = Math.max(lastSignalId, signal.id);
        await handleSignal(signal, roster);
      }
      lastSignalId = Math.max(lastSignalId, result.latest_signal_id);
      status.textContent = roster.length > 1 ? `${roster.length} members connected` : "Waiting for your group…";
    } catch (error) {
      if ([403, 409, 410].includes(error.status)) {
        showError(error.status === 403 ? "Your group access changed, so you left the call." : error.message);
        stopPolling();
        localStream?.getTracks().forEach((track) => track.stop());
        peers.forEach(({ connection }) => connection.close());
        peers.clear();
      } else {
        status.textContent = "Trying to reconnect…";
      }
    } finally {
      pollBusy = false;
    }
  }

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  async function join() {
    errorBox.hidden = true;
    joinButton.disabled = true;
    joinButton.textContent = "Requesting devices…";
    if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
      showError("This browser does not support WebRTC calls. Try a current browser over HTTPS.");
      joinButton.disabled = false;
      joinButton.textContent = "Join with camera";
      return;
    }
    try {
      try {
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      } catch (videoError) {
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        showError("Camera access was unavailable, so you joined with audio only.");
      }
      const session = await post(endpoint("joinUrl"));
      myId = session.participant_id;
      window.callIceServers = session.ice_servers;
      localName.textContent = `${session.name} (you)`;
      localVideo.srcObject = localStream;
      videoEnabled = localStream.getVideoTracks().length > 0;
      localPlaceholder.hidden = videoEnabled;
      localVideo.style.visibility = videoEnabled ? "visible" : "hidden";
      cameraButton.disabled = !videoEnabled;
      cameraButton.setAttribute("aria-pressed", String(videoEnabled));
      cameraButton.textContent = videoEnabled ? "Turn camera off" : "Camera unavailable";
      joined = true;
      joinButton.hidden = true;
      micButton.disabled = false;
      leaveButton.disabled = false;
      status.textContent = "Connecting to your group…";
      await refresh();
      pollTimer = window.setInterval(refresh, 1200);
    } catch (error) {
      localStream?.getTracks().forEach((track) => track.stop());
      localStream = null;
      joinButton.disabled = false;
      joinButton.textContent = "Join with camera";
      showError(error.message || "Could not join the call.");
    }
  }

  micButton.addEventListener("click", () => {
    const tracks = localStream?.getAudioTracks() || [];
    if (!tracks.length) return;
    audioMuted = !audioMuted;
    tracks.forEach((track) => { track.enabled = !audioMuted; });
    micButton.textContent = audioMuted ? "Unmute microphone" : "Mute microphone";
    micButton.setAttribute("aria-pressed", String(audioMuted));
    refresh();
  });

  cameraButton.addEventListener("click", () => {
    const tracks = localStream?.getVideoTracks() || [];
    if (!tracks.length) return;
    videoEnabled = !videoEnabled;
    tracks.forEach((track) => { track.enabled = videoEnabled; });
    localPlaceholder.hidden = videoEnabled;
    localVideo.style.visibility = videoEnabled ? "visible" : "hidden";
    cameraButton.textContent = videoEnabled ? "Turn camera off" : "Turn camera on";
    cameraButton.setAttribute("aria-pressed", String(videoEnabled));
    refresh();
  });

  async function leave() {
    if (leaving) return;
    leaving = true;
    stopPolling();
    try { if (joined) await post(endpoint("leaveUrl")); } catch (_) {}
    peers.forEach(({ connection }) => connection.close());
    localStream?.getTracks().forEach((track) => track.stop());
    window.location.assign(endpoint("groupUrl"));
  }

  joinButton.addEventListener("click", join);
  leaveButton.addEventListener("click", leave);
  window.addEventListener("pagehide", () => {
    if (!joined || leaving) return;
    const token = csrf;
    fetch(endpoint("leaveUrl"), {
      method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token }, body: "{}",
    });
    localStream?.getTracks().forEach((track) => track.stop());
  });
})();
