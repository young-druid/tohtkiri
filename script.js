function toggleReplyForm(elId){
  var els = document.getElementsByClassName('reply-wrapper')
  for (var i = 0; i < els.length; i++){
    var divEl = els[i];
    if (divEl.id != elId){
      divEl.style.display = 'none';
    }
  }
  var el = document.getElementById(elId);
  if (el.style.display == 'none') {
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

function setToken(token){
  var els = document.getElementsByClassName('cobweb')
  for (var i = 0; i < els.length; i++){
    var inputEl = els[i];
    inputEl.value = token;
  }
}