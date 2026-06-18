document.addEventListener('DOMContentLoaded', function () {
    // Mobile nav toggle
    var toggle = document.querySelector('.navbar__toggle');
    var links = document.querySelector('.navbar__links');

    if (toggle && links) {
        toggle.addEventListener('click', function () {
            links.classList.toggle('open');
            toggle.classList.toggle('active');
        });

        // Close on outside click
        document.addEventListener('click', function (e) {
            if (!toggle.contains(e.target) && !links.contains(e.target)) {
                links.classList.remove('open');
            }
        });
    }

    // Auto-dismiss flash messages
    var messages = document.querySelectorAll('.message');
    messages.forEach(function (msg) {
        setTimeout(function () {
            msg.style.opacity = '0';
            msg.style.transition = 'opacity 0.3s ease';
            setTimeout(function () { msg.remove(); }, 300);
        }, 4000);
    });
});
