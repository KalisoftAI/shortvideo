document.addEventListener('DOMContentLoaded', function () {
    // FAQ Accordion
    var faqItems = document.querySelectorAll('.faq__item');
    faqItems.forEach(function (item) {
        var question = item.querySelector('.faq__question');
        if (question) {
            question.addEventListener('click', function () {
                var isOpen = item.classList.contains('open');

                faqItems.forEach(function (other) {
                    if (other !== item) {
                        other.classList.remove('open');
                    }
                });

                item.classList.toggle('open');
            });
        }
    });

    // Smooth scroll for anchor links
    var anchorLinks = document.querySelectorAll('a[href^="#"]');
    anchorLinks.forEach(function (anchor) {
        anchor.addEventListener('click', function (e) {
            var targetId = this.getAttribute('href');
            if (targetId === '#') return;
            var target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });

    // Scroll-triggered animations (Intersection Observer)
    var animateElements = document.querySelectorAll('[data-aos]');
    if (animateElements.length && 'IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('aos-animate');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

        animateElements.forEach(function (el) {
            observer.observe(el);
        });
    } else {
        animateElements.forEach(function (el) {
            el.classList.add('aos-animate');
        });
    }

    // Hero entrance animation
    var hero = document.querySelector('[data-aos-hero]');
    if (hero) {
        setTimeout(function () {
            hero.classList.add('aos-animate');
        }, 100);
    }

    // Pricing toggle
    var pricingToggle = document.getElementById('pricingToggle');
    if (pricingToggle) {
        var labels = document.querySelectorAll('.pricing__toggle-label');
        var cards = document.querySelectorAll('.pricing-card');

        pricingToggle.addEventListener('click', function () {
            var isAnnual = pricingToggle.classList.toggle('active');
            labels.forEach(function (l) {
                l.classList.toggle('active', l.dataset.period === (isAnnual ? 'annual' : 'monthly'));
            });
            cards.forEach(function (card) {
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
        });

        labels.forEach(function (label) {
            label.addEventListener('click', function () {
                var isAnnual = this.dataset.period === 'annual';
                if ((isAnnual && !pricingToggle.classList.contains('active')) ||
                    (!isAnnual && pricingToggle.classList.contains('active'))) {
                    pricingToggle.click();
                }
            });
        });
    }

    // Back to top button
    var backToTop = document.getElementById('backToTop');
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
});
