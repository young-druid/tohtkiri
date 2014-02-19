function toggleReplyForm(elId){
  var el = document.getElementById(elId);
  if (el.style.display == 'none') {
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}