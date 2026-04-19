// Shared proctoring: camera/mic capture, tab-switch violations, optional multi-face pause, video upload.
(function () {
  const MAX_VIOLATIONS = 3;

  class ZyraProctor {
    constructor(options = {}) {
      this.assessmentType = options.assessmentType || 'assessment';
      this.videoElement = options.videoElement || null;
      this.onAutoSubmit = options.onAutoSubmit || function () {};
      this.onPauseChange = options.onPauseChange || function () {};
      this.stream = null;
      this.recorder = null;
      this.chunks = [];
      this.violations = 0;
      this.multipleUserEvents = 0;
      this.faceDetector = null;
      this.faceInterval = null;
      this.pausedForMultipleFaces = false;
      this.stopped = false;
      this.autoSubmitStarted = false;
    }

    async start() {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Camera and microphone are required for this test.');
      }

      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: true
      });

      if (this.videoElement) {
        this.videoElement.srcObject = this.stream;
        this.videoElement.muted = true;
        await this.videoElement.play().catch(() => {});
      }

      this.startRecording();
      this.bindViolationEvents();
      this.startFaceDetection();
      this.updateStatus('Camera and microphone active');
      return this;
    }

    startRecording() {
      if (!window.MediaRecorder || !this.stream) return;
      const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus')
        ? 'video/webm;codecs=vp8,opus'
        : 'video/webm';
      this.recorder = new MediaRecorder(this.stream, { mimeType });
      this.recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) this.chunks.push(event.data);
      };
      this.recorder.start(1000);
    }

    bindViolationEvents() {
      this.visibilityHandler = () => {
        if (document.hidden) this.registerViolation('Tab switched or browser minimized');
      };
      this.blurHandler = () => this.registerViolation('Window focus lost');
      document.addEventListener('visibilitychange', this.visibilityHandler);
      window.addEventListener('blur', this.blurHandler);
    }

    startFaceDetection() {
      if (!('FaceDetector' in window) || !this.videoElement) {
        this.updateFaceStatus('Multi-user detection unavailable in this browser');
        return;
      }

      try {
        this.faceDetector = new FaceDetector({ fastMode: true, maxDetectedFaces: 4 });
      } catch {
        this.updateFaceStatus('Multi-user detection unavailable in this browser');
        return;
      }

      this.faceInterval = setInterval(async () => {
        if (!this.videoElement || this.videoElement.readyState < 2 || this.stopped) return;
        try {
          const faces = await this.faceDetector.detect(this.videoElement);
          if (faces.length > 1) {
            this.multipleUserEvents++;
            this.setPausedForMultipleFaces(true);
          } else {
            this.setPausedForMultipleFaces(false);
          }
        } catch {
          this.updateFaceStatus('Face scan paused');
        }
      }, 1800);
    }

    registerViolation(reason) {
      if (this.stopped || this.autoSubmitStarted) return;
      this.violations++;
      this.updateStatus(`${reason}. Warning ${this.violations}/${MAX_VIOLATIONS}`);
      if (this.violations >= MAX_VIOLATIONS) {
        this.autoSubmitStarted = true;
        this.updateStatus('Violation limit exceeded. Auto-submitting test...');
        document.body.classList.add('proctor-auto-submitting');
        this.onAutoSubmit({ reason, violations: this.violations });
      }
    }

    setPausedForMultipleFaces(paused) {
      if (this.pausedForMultipleFaces === paused) return;
      this.pausedForMultipleFaces = paused;
      document.body.classList.toggle('proctor-paused', paused);
      this.updateFaceStatus(paused ? 'Multiple users detected. Test paused.' : 'Single user detected');
      this.onPauseChange(paused);
    }

    updateStatus(text) {
      const el = document.getElementById('proctor-status');
      if (el) el.textContent = text;
      const count = document.getElementById('proctor-violations');
      if (count) count.textContent = String(this.violations);
    }

    updateFaceStatus(text) {
      const el = document.getElementById('proctor-face-status');
      if (el) el.textContent = text;
    }

    async stopAndUpload(extra = {}) {
      if (this.stopped) return null;
      this.stopped = true;
      document.removeEventListener('visibilitychange', this.visibilityHandler);
      window.removeEventListener('blur', this.blurHandler);
      if (this.faceInterval) clearInterval(this.faceInterval);

      const blob = await this.stopRecorder();
      this.stream?.getTracks().forEach(track => track.stop());
      if (this.videoElement) this.videoElement.srcObject = null;
      if (!blob || blob.size === 0) return null;

      const form = new FormData();
      form.append('video', blob, `${this.assessmentType}-proctoring.webm`);
      form.append('assessment_type', this.assessmentType);
      form.append('violations', String(this.violations));
      form.append('multiple_user_events', String(this.multipleUserEvents));
      form.append('metadata', JSON.stringify(extra || {}));

      try {
        const response = await fetch('/api/proctoring/upload', { method: 'POST', body: form });
        return await response.json().catch(() => null);
      } catch {
        return null;
      }
    }

    stopRecorder() {
      return new Promise(resolve => {
        if (!this.recorder || this.recorder.state === 'inactive') {
          resolve(this.chunks.length ? new Blob(this.chunks, { type: 'video/webm' }) : null);
          return;
        }
        this.recorder.onstop = () => resolve(new Blob(this.chunks, { type: 'video/webm' }));
        this.recorder.stop();
      });
    }
  }

  window.ZyraProctor = ZyraProctor;
})();
