document.addEventListener('DOMContentLoaded', function () {
    // Navbar toggle
    const toggle = document.querySelector('.navbar__toggle');
    const links = document.querySelector('.navbar__links');
    if (toggle && links) {
        toggle.addEventListener('click', function () {
            this.classList.toggle('active');
            links.classList.toggle('open');
        });
    }

    // AOS scroll animations
    const aosEls = document.querySelectorAll('[data-aos]');
    if (aosEls.length && 'IntersectionObserver' in window) {
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('aos-animate');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });
        aosEls.forEach(function (el) { observer.observe(el); });
    } else {
        aosEls.forEach(function (el) { el.classList.add('aos-animate'); });
    }

    // Hero stagger animation
    const hero = document.querySelector('[data-aos-hero]');
    if (hero && 'IntersectionObserver' in window) {
        const heroObserver = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('aos-animate');
                    heroObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        heroObserver.observe(hero);
    } else if (hero) {
        hero.classList.add('aos-animate');
    }

    // Back to top button
    const backToTop = document.getElementById('backToTop');
    if (backToTop) {
        window.addEventListener('scroll', function () {
            if (window.scrollY > 400) {
                backToTop.classList.add('visible');
            } else {
                backToTop.classList.remove('visible');
            }
        });
        backToTop.addEventListener('click', function () {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    // FAQ accordion
    document.querySelectorAll('.faq__question').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const item = this.closest('.faq__item');
            if (item) {
                item.classList.toggle('open');
            }
        });
    });

    // Pricing toggle (landing page)
    const pricingToggle = document.getElementById('pricingToggle');
    const pricingLabels = document.querySelectorAll('.pricing__toggle-label');
    const pricingCards = document.querySelectorAll('.pricing-card');

    function updatePrices(isAnnual) {
        pricingCards.forEach(function (card) {
            var amount = card.querySelector('.pricing-card__amount');
            var period = card.querySelector('.pricing-card__period');
            if (isAnnual) {
                amount.textContent = card.dataset.annual;
                period.textContent = '/month, billed annually';
            } else {
                amount.textContent = card.dataset.monthly;
                period.textContent = '/month';
            }
        });
    }

    if (pricingToggle) {
        pricingToggle.addEventListener('click', function () {
            var isAnnual = pricingToggle.classList.toggle('active');
            pricingLabels.forEach(function (l) {
                l.classList.toggle('active', l.dataset.period === (isAnnual ? 'annual' : 'monthly'));
            });
            updatePrices(isAnnual);
        });

        pricingLabels.forEach(function (label) {
            label.addEventListener('click', function () {
                var isAnnual = this.dataset.period === 'annual';
                if ((isAnnual && !pricingToggle.classList.contains('active')) ||
                    (!isAnnual && pricingToggle.classList.contains('active'))) {
                    pricingToggle.click();
                }
            });
        });
    }
});
