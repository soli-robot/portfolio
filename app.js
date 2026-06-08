document.addEventListener('DOMContentLoaded', () => {
    // 1. Theme Toggle
    const themeToggleBtn = document.getElementById('themeToggle');
    const body = document.body;
    
    // Check saved theme
    const savedTheme = localStorage.getItem('portfolio-theme') || 'dark';
    if (savedTheme === 'light') {
        body.classList.remove('dark-theme');
        body.classList.add('light-theme');
    } else {
        body.classList.add('dark-theme');
        body.classList.remove('light-theme');
    }

    themeToggleBtn.addEventListener('click', () => {
        if (body.classList.contains('dark-theme')) {
            body.classList.replace('dark-theme', 'light-theme');
            localStorage.setItem('portfolio-theme', 'light');
            showToast('라이트 모드로 전환되었습니다.', 'success');
        } else {
            body.classList.replace('light-theme', 'dark-theme');
            localStorage.setItem('portfolio-theme', 'dark');
            showToast('다크 모드로 전환되었습니다.', 'success');
        }
    });

    // 2. Mouse Glow Effect
    const glow = document.getElementById('pointerGlow');
    window.addEventListener('mousemove', (e) => {
        const x = e.clientX;
        const y = e.clientY;
        glow.style.setProperty('--x', `${x}px`);
        glow.style.setProperty('--y', `${y}px`);
    });

    // 3. Scroll Reveal Animation
    const revealElements = document.querySelectorAll('.scroll-reveal');
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('active');
            }
        });
    }, {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    });

    revealElements.forEach(el => observer.observe(el));

    // 4. Mobile Menu Toggle
    const mobileMenuBtn = document.getElementById('mobileMenuBtn');
    const mobileMenu = document.getElementById('mobileMenu');
    
    mobileMenuBtn.addEventListener('click', () => {
        mobileMenu.classList.toggle('active');
        const isOpen = mobileMenu.classList.contains('active');
        mobileMenuBtn.innerHTML = isOpen ? '<i data-lucide="x"></i>' : '<i data-lucide="menu"></i>';
        lucide.createIcons();
    });

    // Close mobile menu when a link is clicked
    document.querySelectorAll('.mobile-link').forEach(link => {
        link.addEventListener('click', () => {
            mobileMenu.classList.remove('active');
            mobileMenuBtn.innerHTML = '<i data-lucide="menu"></i>';
            lucide.createIcons();
        });
    });

    // 5. Active Navigation Link on Scroll
    const sections = document.querySelectorAll('section');
    const navLinks = document.querySelectorAll('.nav-link');

    window.addEventListener('scroll', () => {
        let current = '';
        sections.forEach(section => {
            const sectionTop = section.offsetTop;
            const sectionHeight = section.clientHeight;
            if (pageYOffset >= (sectionTop - 150)) {
                current = section.getAttribute('id');
            }
        });

        navLinks.forEach(link => {
            link.classList.remove('active');
            if (link.getAttribute('href').slice(1) === current) {
                link.classList.add('active');
            }
        });
    });

    // 6. Copy Email Feature
    const copyEmailBtn = document.getElementById('copyEmailBtn');
    const emailText = document.getElementById('emailText').textContent;

    copyEmailBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(emailText).then(() => {
            showToast('이메일 주소가 클립보드에 복사되었습니다.', 'success');
        }).catch(err => {
            console.error('클립보드 복사 실패:', err);
            showToast('이메일 주소를 복사하지 못했습니다.', 'error');
        });
    });

    // 7. Contact Form Handling (Mock)
    const contactForm = document.getElementById('contactForm');
    contactForm.addEventListener('submit', (e) => {
        e.preventDefault();
        
        const name = document.getElementById('name').value;
        const email = document.getElementById('email').value;
        const message = document.getElementById('message').value;

        if (!name || !email || !message) {
            showToast('모든 빈칸을 채워주세요.', 'error');
            return;
        }

        showToast('메시지가 성공적으로 전송되었습니다! (데모)', 'success');
        contactForm.reset();
    });

    // 8. Video Modal Logic
    const videoModal = document.getElementById('videoModal');
    const modalVideoPlayer = document.getElementById('modalVideoPlayer');
    const videoTitle = document.getElementById('videoTitle');
    const closeVideoBtn = document.getElementById('closeVideoBtn');
    const playButtons = document.querySelectorAll('.play-video-btn');

    playButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const videoSrc = btn.getAttribute('data-video');
            const title = btn.getAttribute('data-title');
            
            videoTitle.textContent = title;
            modalVideoPlayer.src = videoSrc;
            videoModal.classList.remove('hidden');
            setTimeout(() => {
                videoModal.classList.add('show');
            }, 10);
            modalVideoPlayer.play();
        });
    });

    function closeVideoModal() {
        videoModal.classList.remove('show');
        setTimeout(() => {
            videoModal.classList.add('hidden');
            modalVideoPlayer.pause();
            modalVideoPlayer.src = '';
        }, 300);
    }

    closeVideoBtn.addEventListener('click', closeVideoModal);
    videoModal.addEventListener('click', (e) => {
        if (e.target === videoModal) {
            closeVideoModal();
        }
    });

    // 9. Toast Helper
    const toast = document.getElementById('toast');
    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = `toast show toast-${type}`;
        setTimeout(() => {
            toast.classList.remove('show');
        }, 3000);
    }

    // Load Lucide Icons for dynamic content
    lucide.createIcons();
});
