jQuery(function ($) {

    function loadDocs(parent, child = '') {
        $.ajax({
            url: docs_ajax.url,
            method: 'POST',
            data: {
                action: 'get_docs',
                parent: parent,
                child: child
            },
            success: function (response) {
                $('#docs-content').html(response);
            }
        });
    }

    // верхние табы
    $(document).on('click', '.tab-parent', function (e) {
        e.preventDefault();

        $('.tab-parent').removeClass('active');
        $(this).addClass('active');

        let parent = $(this).data('parent');

        loadDocs(parent);
    });

    // нижние табы
    $(document).on('click', '#docs-content .tab-link', function (e) {
        e.preventDefault();

        $('#docs-content .tab-link').removeClass('active');
        $(this).addClass('active');

        let parent = $('.tab-parent.active').data('parent');
        let child = $(this).data('child');

        loadDocs(parent, child);
    });

    // загрузка первого таба по умолчанию
    $(document).ready(function () {
        let first = $('.tab-parent.active').data('parent');
        if (first) {
            // дочерний таб из ссылки ?doc_cat=<ID>, если он задан
            loadDocs(first, $('#docs-content').data('initial-child') || '');
        }
    });

});